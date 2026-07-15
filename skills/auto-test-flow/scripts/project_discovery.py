"""Local project context discovery for auto-test-flow."""

from __future__ import annotations

import json
import re
from pathlib import Path


def discover_project_context(
    *,
    raw_requirement: str,
    requirement_text: str,
    fields: dict,
    cwd: Path,
) -> dict:
    """Discover existing project structure before generating plan and cases."""
    project_root = select_project_root(
        raw_requirement=raw_requirement,
        requirement_text=requirement_text,
        fields=fields,
        cwd=cwd,
    )
    if not project_root:
        return {
            "status": "not_found",
            "project_root": None,
            "framework_signals": [],
            "relevant_files": [],
            "key_snippets": [],
            "recommended_commands": [],
            "hard_constraints": [
                "No local project root was discovered before artifact generation.",
                "Downstream artifacts must ask Codex to inspect the repository before proposing files, classes, selectors, fixtures, or commands.",
                "Do not invent project paths, page objects, selectors, fixtures, or execution commands.",
            ],
            "forbidden_patterns": [],
            "notes": [
                "Provide a concrete project path in the requirement for stronger project-aware generation.",
            ],
        }

    return collect_project_context(
        project_root=project_root,
        raw_requirement=raw_requirement,
        requirement_text=requirement_text,
        fields=fields,
    )


def select_project_root(
    *,
    raw_requirement: str,
    requirement_text: str,
    fields: dict,
    cwd: Path,
) -> Path | None:
    """Pick the strongest local project root candidate without using the API."""
    candidates = []
    source_text = "\n".join([
        raw_requirement or "",
        requirement_text or "",
        json.dumps(fields, ensure_ascii=False),
    ])

    candidates.extend(extract_path_candidates(source_text))

    for base in [cwd, *cwd.parents]:
        candidates.append(base / "auto-test")
        candidates.append(base)

    seen = set()
    scored = []
    for candidate in candidates:
        root = normalize_project_root(candidate)
        if not root:
            continue
        key = str(root).lower()
        if key in seen:
            continue
        seen.add(key)
        score = score_project_root(root)
        if score > 0:
            scored.append((score, root))

    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], len(str(item[1]))), reverse=True)
    return scored[0][1]


def extract_path_candidates(text: str) -> list[Path]:
    """Extract Windows path candidates and trim natural-language suffixes."""
    candidates = []
    for match in re.finditer(r"[A-Za-z]:\\[^\r\n\"'<>|]+", text or ""):
        raw_candidate = match.group(0).strip().rstrip(".,;:，。；：)）]")
        resolved = longest_existing_path_prefix(raw_candidate)
        if resolved:
            candidates.append(resolved)
        else:
            candidates.append(Path(raw_candidate))
    return candidates


def longest_existing_path_prefix(raw_path: str) -> Path | None:
    """Return the longest existing prefix from a path-like string."""
    cleaned = raw_path.strip().rstrip(".,;:，。；：)）]")
    if not cleaned:
        return None
    try:
        if Path(cleaned).exists():
            return Path(cleaned)
    except OSError:
        pass

    min_len = 3  # e.g. C:\
    for end in range(len(cleaned) - 1, min_len - 1, -1):
        prefix = cleaned[:end].strip().rstrip(".,;:，。；：)）]")
        if len(prefix) < min_len:
            continue
        try:
            if Path(prefix).exists():
                return Path(prefix)
        except OSError:
            continue
    return None


def normalize_project_root(path: Path) -> Path | None:
    try:
        if not path.exists():
            return None
        current = path if path.is_dir() else path.parent
        markers = {
            "pytest.ini",
            "pyproject.toml",
            "package.json",
            "pom.xml",
            "build.gradle",
            "project",
            "tests",
            "src",
        }
        for candidate in [current, *current.parents]:
            if any((candidate / marker).exists() for marker in markers):
                return candidate.resolve()
        return current.resolve()
    except OSError:
        return None


def score_project_root(root: Path) -> int:
    score = 0
    marker_weights = {
        "pytest.ini": 6,
        "pyproject.toml": 5,
        "package.json": 5,
        "conftest.py": 4,
        "runner.py": 3,
        "project": 4,
        "tests": 3,
        "src": 2,
    }
    for marker, weight in marker_weights.items():
        if (root / marker).exists():
            score += weight
    if root.name.lower() == "auto-test":
        score += 8
    if (root / "project" / "feikua").exists():
        score += 8
    return score


def collect_project_context(
    *,
    project_root: Path,
    raw_requirement: str,
    requirement_text: str,
    fields: dict,
) -> dict:
    terms = derive_discovery_terms(
        raw_requirement=raw_requirement,
        requirement_text=requirement_text,
        fields=fields,
    )
    framework_signals = discover_framework_signals(project_root)
    relevant_files = find_relevant_files(project_root, terms)
    snippets = [
        build_file_snippet(project_root / item["path"], terms)
        for item in relevant_files[:10]
    ]
    snippets = [item for item in snippets if item]
    recommended_commands = infer_recommended_commands(project_root, relevant_files)
    hard_constraints, forbidden_patterns, notes = infer_project_constraints(
        relevant_files=relevant_files,
        terms=terms,
    )
    test_flow_readiness = build_test_flow_readiness(
        project_root=project_root,
        terms=terms,
        relevant_files=relevant_files,
        framework_signals=framework_signals,
    )
    return {
        "status": "discovered",
        "project_root": str(project_root),
        "framework_signals": framework_signals,
        "relevant_files": relevant_files[:20],
        "key_snippets": snippets,
        "recommended_commands": recommended_commands,
        "hard_constraints": hard_constraints,
        "forbidden_patterns": forbidden_patterns,
        "notes": notes,
        "test_flow_readiness": test_flow_readiness,
    }


def derive_discovery_terms(
    *,
    raw_requirement: str,
    requirement_text: str,
    fields: dict,
) -> list[str]:
    source = "\n".join([
        raw_requirement or "",
        requirement_text or "",
        json.dumps(fields, ensure_ascii=False),
    ]).lower()
    terms = set(re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", source))
    terms.update(re.findall(r"[\u4e00-\u9fff]{2,}", source))

    if any(token in source for token in ["finger", "fingerprint", "finger_print", "指纹"]):
        terms.update([
            "finger",
            "fingerprint",
            "finger_print",
            "指纹",
            "screen",
            "timezone",
            "language",
            "cpu",
            "device",
            "device_memory",
            "deviceMemory",
            "browser",
            "store",
        ])
    if "screen" in source or "屏幕" in source:
        terms.update(["screen", "屏幕"])
    if "timezone" in source or "时区" in source:
        terms.update(["timezone", "时区"])
    if "language" in source or "语言" in source:
        terms.update(["language", "语言"])

    noisy = {"the", "and", "for", "with", "this", "that", "json", "true", "false"}
    return sorted(term for term in terms if term not in noisy)


def discover_framework_signals(project_root: Path) -> list[dict]:
    signal_names = [
        "pytest.ini",
        "conftest.py",
        "requirements.txt",
        "requirements",
        "pyproject.toml",
        "package.json",
        "playwright.config.ts",
        "playwright.config.js",
        "cypress.config.ts",
        "cypress.config.js",
        "runner.py",
    ]
    signals = []
    for name in signal_names:
        path = project_root / name
        if path.exists():
            signals.append({"path": name, "type": "file" if path.is_file() else "directory"})
    return signals


def find_relevant_files(project_root: Path, terms: list[str]) -> list[dict]:
    scored_files = []
    allowed_suffixes = {".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".json", ".ini", ".toml", ".yaml", ".yml"}
    ignored_parts = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        "allure-results",
        "allure-report",
        "logs",
        "output",
        "report",
        "reports",
        "testreport",
        "dist",
        "build",
    }

    for path in iter_project_files(project_root):
        relative = path.relative_to(project_root)
        parts = {part.lower() for part in relative.parts}
        if parts & ignored_parts:
            continue
        if path.suffix.lower() not in allowed_suffixes:
            continue
        rel_text = str(relative).replace("\\", "/").lower()
        score = 0
        matched = set()
        for term in terms:
            lowered = term.lower()
            if lowered and lowered in rel_text:
                score += 8
                matched.add(term)
        score += score_file_content(path, terms, matched)
        if score:
            scored_files.append({
                "path": str(relative).replace("\\", "/"),
                "score": score,
                "matched_terms": sorted(matched)[:12],
            })

    scored_files.sort(key=lambda item: (item["score"], -len(item["path"])), reverse=True)
    return scored_files


def iter_project_files(project_root: Path):
    try:
        for path in project_root.rglob("*"):
            if path.is_file():
                yield path
    except OSError:
        return


def score_file_content(path: Path, terms: list[str], matched: set) -> int:
    try:
        if path.stat().st_size > 250_000:
            return 0
        text = path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return 0
    score = 0
    for term in terms:
        lowered = term.lower()
        if lowered and lowered in text:
            count = min(text.count(lowered), 5)
            score += count
            matched.add(term)
    return score


def build_file_snippet(path: Path, terms: list[str]) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    term_lowers = [term.lower() for term in terms if term]
    matched_indexes = []
    matched_terms = set()
    for index, line in enumerate(lines):
        lowered = line.lower()
        line_terms = [term for term in term_lowers if term in lowered]
        if line_terms:
            matched_indexes.append(index)
            matched_terms.update(line_terms[:6])
        if len(matched_indexes) >= 6:
            break

    if not matched_indexes:
        selected_indexes = range(0, min(len(lines), 35))
    else:
        indexes = set()
        for index in matched_indexes[:4]:
            indexes.update(range(max(0, index - 2), min(len(lines), index + 3)))
        selected_indexes = sorted(indexes)[:50]

    snippet_lines = [
        f"{index + 1}: {lines[index]}"
        for index in selected_indexes
    ]
    return {
        "path": str(path),
        "matched_terms": sorted(matched_terms)[:12],
        "snippet": "\n".join(snippet_lines),
    }


def infer_recommended_commands(project_root: Path, relevant_files: list[dict]) -> list[str]:
    commands = []
    relevant_paths = [item.get("path", "") for item in relevant_files]
    fingerprint_test = "project/feikua/testcases/test_finger_print.py"
    if fingerprint_test in relevant_paths:
        commands.append(
            r"venv\Scripts\python.exe -m pytest project\feikua\testcases\test_finger_print.py::TestFingerPrintCompare::test_compare_browser_and_store_fingerprints -q --reruns 0"
        )
    elif (project_root / "pytest.ini").exists():
        first_test = next((path for path in relevant_paths if path.endswith(".py") and "/test" in path), None)
        if first_test:
            first_test_windows = first_test.replace("/", "\\")
            commands.append(fr"venv\Scripts\python.exe -m pytest {first_test_windows} -q")
        else:
            commands.append(r"venv\Scripts\python.exe -m pytest -q")
    if (project_root / "package.json").exists():
        commands.append("npm test")
    return commands


def infer_project_constraints(
    *,
    relevant_files: list[dict],
    terms: list[str],
) -> tuple[list[str], list[str], list[str]]:
    relevant_paths = {item.get("path", "") for item in relevant_files}
    hard_constraints = [
        "Treat project_context_discovery as the source of truth for file layout, framework, and execution commands.",
        "Prefer existing files, page objects, selectors, fixtures, helpers, naming, and assertion style over introducing new structure.",
        "Do not propose new files, classes, selectors, fixtures, or commands that conflict with discovered project files.",
    ]
    forbidden_patterns = []
    notes = []
    term_text = " ".join(terms).lower()

    if "finger" in term_text or "fingerprint" in term_text or "finger_print" in term_text or "指纹" in term_text:
        if "project/feikua/testcases/test_finger_print.py" in relevant_paths:
            hard_constraints.append("Reuse project/feikua/testcases/test_finger_print.py for fingerprint automation unless the user explicitly approves a new test file.")
        if "project/feikua/pages/login_page/finger_print_page/finger_print_page.py" in relevant_paths:
            hard_constraints.append("Reuse FingerPrintPage in project/feikua/pages/login_page/finger_print_page/finger_print_page.py for fingerprint page operations.")
        if "project/feikua/selectors/finger_print_selectors.py" in relevant_paths:
            hard_constraints.append("Reuse and extend FingerprintSelectors in project/feikua/selectors/finger_print_selectors.py for fingerprint locators.")
        hard_constraints.append("Preserve existing fingerprint assertion logic: enabled spoofing means store value differs from the real browser; disabled spoofing means store value equals the real browser.")
        hard_constraints.append("Keep first implementation scoped to screen, timezone, and language unless the confirmed requirement expands scope.")
        forbidden_patterns.extend([
            "ShopEditPage",
            "test_fingerprint_screen_timezone_language.py",
            "pytest tests/",
            "new fixture",
            "new selector file",
        ])
        notes.append("Fingerprint-specific constraints were inferred from discovered feikua fingerprint files.")

    if not relevant_files:
        notes.append("No relevant files matched the requirement terms; downstream artifacts must request more project inspection.")

    return hard_constraints, forbidden_patterns, notes


def build_test_flow_readiness(
    *,
    project_root: Path,
    terms: list[str],
    relevant_files: list[dict],
    framework_signals: list[dict],
) -> dict:
    """Summarize what the local code already proves about test-flow generation."""
    relevant_paths = [item.get("path", "") for item in relevant_files]
    framework = infer_framework_summary(project_root, framework_signals)
    fixtures = discover_pytest_fixtures(project_root / "conftest.py")
    page_objects = discover_named_symbols(project_root / "project", "pages", r"class\s+([A-Za-z_][A-Za-z0-9_]*Page)\b")
    selector_classes = discover_named_symbols(project_root / "project", "selectors", r"class\s+([A-Za-z_][A-Za-z0-9_]*Selectors?)\b")
    matched_existing_tests = discover_matching_test_symbols(project_root, relevant_paths)
    matched_existing_methods = discover_matching_methods(project_root, relevant_paths, terms)

    known_flow_fragments = []
    missing_for_test_flow = [
        "真实业务目标和最终断言",
        "本次是否允许修改测试环境数据或保存店铺配置",
        "需要点击、读取或断言的新 UI 元素的 DOM/CDP/F12 证据",
    ]
    if "project/feikua/testcases/test_finger_print.py" in relevant_paths:
        known_flow_fragments.extend([
            "启动 Electron 并连接主窗口 CDP 9333",
            "进入店铺列表并搜索目标店铺",
            "进入编辑店铺 -> 店铺环境配置 -> 专家配置",
            "打开店铺浏览器 CDP 9222 并访问 BrowserLeaks 页面",
            "读取店铺浏览器实际指纹值并执行断言",
        ])
    if any("font" in term.lower() or "字体" in term for term in terms):
        if "project/feikua/testcases/test_finger_print.py" in relevant_paths:
            known_flow_fragments.append("已有 font 指纹用例雏形: test_font_fingerprint_mode")
        missing_for_test_flow.extend([
            "fontFlag=0/1/2 的业务含义",
            "font 用例是复用、补强还是改写现有 test_font_fingerprint_mode",
            "font 断言是否只校验 BrowserLeaks 输出非空，还是必须校验配置字体列表",
        ])

    flow_status = "needs_confirmation"
    if matched_existing_tests and known_flow_fragments:
        flow_status = "partial_from_existing_code"
    elif not matched_existing_tests:
        flow_status = "blocked_pending_project_mapping"

    return {
        "flow_status": flow_status,
        "framework": framework,
        "fixtures": fixtures,
        "page_objects": page_objects[:20],
        "selector_classes": selector_classes[:20],
        "matched_existing_tests": matched_existing_tests[:12],
        "matched_existing_methods": matched_existing_methods[:20],
        "known_flow_fragments": known_flow_fragments,
        "missing_for_test_flow": dedupe_keep_order(missing_for_test_flow),
        "rule": "Only generate final business test steps when real page flow, code mapping, expected result, and element evidence are confirmed.",
    }


def infer_framework_summary(project_root: Path, framework_signals: list[dict]) -> str:
    signal_paths = {item.get("path") for item in framework_signals}
    parts = []
    if "pytest.ini" in signal_paths or (project_root / "conftest.py").exists():
        parts.append("pytest")
    if file_contains(project_root / "conftest.py", "allure"):
        parts.append("allure")
    if file_contains(project_root / "conftest.py", "sync_playwright"):
        parts.append("playwright")
    if file_contains(project_root / "conftest.py", "electron"):
        parts.append("electron/cdp")
    return " + ".join(parts) if parts else "unknown"


def discover_pytest_fixtures(conftest_path: Path) -> list[str]:
    try:
        text = conftest_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    fixtures = []
    for match in re.finditer(r"@pytest\.fixture[\s\S]{0,120}?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text):
        fixtures.append(match.group(1))
    return dedupe_keep_order(fixtures)


def discover_named_symbols(project_root: Path, folder_name: str, pattern: str) -> list[dict]:
    if not project_root.exists():
        return []
    symbols = []
    regex = re.compile(pattern)
    for path in iter_project_files(project_root):
        rel = str(path.relative_to(project_root)).replace("\\", "/")
        if f"/{folder_name}/" not in f"/{rel}" or path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names = dedupe_keep_order(regex.findall(text))
        for name in names:
            symbols.append({"name": name, "path": rel})
    return symbols


def discover_matching_test_symbols(project_root: Path, relevant_paths: list[str]) -> list[dict]:
    tests = []
    for rel in relevant_paths:
        if "/testcases/" not in rel or not rel.endswith(".py"):
            continue
        path = project_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        classes = re.findall(r"^class\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE)
        functions = re.findall(r"^\s+def\s+(test_[A-Za-z0-9_]+)\s*\(", text, flags=re.MULTILINE)
        tests.append({
            "path": rel,
            "classes": classes[:5],
            "tests": functions[:12],
        })
    return tests


def discover_matching_methods(project_root: Path, relevant_paths: list[str], terms: list[str]) -> list[dict]:
    methods = []
    term_lowers = [term.lower() for term in terms if len(term) >= 3]
    for rel in relevant_paths:
        if not rel.endswith(".py") or not any(part in rel for part in ["/pages/", "/selectors/", "/testcases/"]):
            continue
        path = project_root / rel
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        names = re.findall(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", text, flags=re.MULTILINE)
        constants = re.findall(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", text, flags=re.MULTILINE)
        for name in [*names, *constants]:
            lowered = name.lower()
            if any(term in lowered for term in term_lowers):
                methods.append({"name": name, "path": rel})
    return methods


def file_contains(path: Path, needle: str) -> bool:
    try:
        return needle.lower() in path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return False


def dedupe_keep_order(items: list) -> list:
    seen = set()
    result = []
    for item in items:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def build_project_context_discovery_markdown(context: dict) -> str:
    context = context or {}
    lines = [
        "# Project Context Discovery",
        "",
        f"- Status: {context.get('status', 'not_run')}",
        f"- Project root: {context.get('project_root') or 'not discovered'}",
        "",
        "## Framework Signals",
    ]
    signals = context.get("framework_signals", [])
    lines.extend(markdown_bullets(
        f"{item.get('path')} ({item.get('type')})" for item in signals
    ))
    lines.extend(["", "## Relevant Files"])
    lines.extend(markdown_bullets(
        f"{item.get('path')} - score {item.get('score')} - matched: {', '.join(item.get('matched_terms', []))}"
        for item in context.get("relevant_files", [])
    ))
    lines.extend(["", "## Recommended Commands"])
    lines.extend(markdown_bullets(context.get("recommended_commands", [])))
    lines.extend(["", "## Hard Constraints"])
    lines.extend(markdown_bullets(context.get("hard_constraints", [])))
    readiness = context.get("test_flow_readiness", {})
    if readiness:
        lines.extend([
            "",
            "## Test Flow Readiness",
            "",
            f"- Flow status: {readiness.get('flow_status', 'unknown')}",
            f"- Framework: {readiness.get('framework', 'unknown')}",
            "",
            "### Known Flow Fragments",
        ])
        lines.extend(markdown_bullets(readiness.get("known_flow_fragments", [])))
        lines.extend(["", "### Missing For Final Test Flow"])
        lines.extend(markdown_bullets(readiness.get("missing_for_test_flow", [])))
    lines.extend(["", "## Key Snippets"])
    for snippet in context.get("key_snippets", []):
        lines.extend([
            "",
            f"### {snippet.get('path')}",
            "",
            "```text",
            snippet.get("snippet", ""),
            "```",
        ])
    return "\n".join(lines)


def markdown_bullets(items) -> list[str]:
    values = [str(item) for item in items if item]
    if not values:
        return ["- None"]
    return [f"- {item}" for item in values]
