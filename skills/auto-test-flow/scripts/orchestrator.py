#!/usr/bin/env python3
"""Phase 2.6: 自动化测试工作流编排器

流程:
    粗略需求
    → intake 校验
    → 结构化字段抽取
    → 测试方案生成
    → 测试用例结构化
    → 自动化脚本生成请求
    → 执行请求
    → 自动审查与权限 gate
    → Codex handoff 任务包
    → 输出报告

用法:
    python orchestrator.py "对登录页面进行功能测试"
    python orchestrator.py --file requirements.txt
    python orchestrator.py "输入需求" --output-dir ./output
"""

import argparse
import functools
import http.server
import json
import os
import re
import socketserver
import sys
from datetime import datetime
from pathlib import Path

from exporters import write_test_cases_xlsx, write_test_cases_xmind
from viewer import build_html_viewer

from config import (
    ANTHROPIC_AUTH_TOKEN,
    ANTHROPIC_BASE_URL,
    MODEL,
    PLAN_TEMPERATURE,
    PLAN_MAX_TOKENS,
)
from templates.test_plan_prompt import (
    AUTOMATION_REQUEST_JSON_SYSTEM_PROMPT,
    CODEX_HANDOFF_REQUIREMENTS,
    EXECUTION_REQUEST_JSON_SYSTEM_PROMPT,
    REVIEW_POLICY_GUIDE,
    TEST_CASES_JSON_SYSTEM_PROMPT,
    TEST_PLAN_SYSTEM_PROMPT,
)


REVIEW_POLICIES = ("ask", "auto-review", "full-auto")


EXTRACT_FIELDS_SYSTEM_PROMPT = """你是一个需求分析师。请从以下测试需求上下文中，提取出关键字段。
返回纯JSON格式（不要带```json标记），包含以下字段：
{
    "test_object": "测试对象说明",
    "business_context": "业务背景说明",
    "core_requirements": "核心需求列表，用换行符分隔",
    "user_roles": "用户角色列表，用换行符分隔",
    "input_conditions": "输入条件列表，用换行符分隔",
    "expected_results": "预期结果说明",
    "exception_scenarios": "异常情况列表，用换行符分隔"
}"""


INTAKE_VALIDATION_SYSTEM_PROMPT = """你是自动化测试需求 intake 审查员。你的任务不是扩写需求，而是判断用户输入和附件摘要中是否能识别出一个可用于生成测试计划/测试用例的测试点。

请只返回纯 JSON，不要输出 markdown，不要输出 ```json。

返回格式：
{
  "status": "ready 或 needs_clarification",
  "normalized_requirement": "当 status=ready 时，基于用户输入和材料摘要整理出保守的需求上下文；不要编造页面、账号、步骤、预期",
  "reason": "简短原因",
  "questions": ["当 status=needs_clarification 时，列出需要用户补充的问题"]
}

判定规则：
- 这是“生成测试计划/测试用例”的入口，不是“直接写自动化代码”的入口；不要用代码实现所需的严格程度来阻断用例生成。
- 只要能从用户输入或附件摘要中识别出测试对象/系统/模块/功能点，并能找到相关测试说明、URL、规则、场景、状态、预期倾向或测试点描述，就返回 ready。
- 如果用户说“根据文档/表格里的某功能点生成测试用例”，且附件摘要里存在对应功能点行或相关上下文，应返回 ready；缺少的细节放到后续 need_confirmation，不要在 intake 阶段阻断。
- 如果只是数字、编号、单词、泛泛一句话、只有“测试一下”，且附件摘要也无法定位测试对象或测试点，则返回 needs_clarification。
- 如果缺少关键信息但仍能生成测试用例草案，返回 ready，并把缺失信息写入 normalized_requirement 的“待确认”部分。
- 不要为了继续流程而猜测业务背景、页面元素、选择器、账号、团队、环境、预期结果。
"""


class TestWorkflowOrchestrator:
    """Phase 2.6 orchestrator with review policy and Codex handoff."""

    def __init__(self):
        from anthropic import Anthropic

        self.client = Anthropic(
            auth_token=ANTHROPIC_AUTH_TOKEN,
            base_url=ANTHROPIC_BASE_URL,
        )
        self.intake_validation = {}
        self.requirement_text = ""
        self.fields = {}
        self.project_context_discovery = {}
        self.test_plan = ""
        self.test_cases = {}
        self.automation_request = {}
        self.execution_request = {}
        self.project_context_request = {}
        self.codex_task = {}
        self.review_policy = "auto-review"
        self.review_result = {}
        self.full_artifacts = False

    def _call_api(self, system_prompt: str, user_message: str,
                  temperature: float, max_tokens: int) -> str:
        """Call Anthropic-compatible API."""
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_prompt,
            messages=[{"role": "user", "content": user_message}],
        )
        # Extract text from response, skipping ThinkingBlock.
        for block in response.content:
            if hasattr(block, "text"):
                return block.text
        raise ValueError(f"No text block in response: {response.content}")

    def step_validate_intake(self, raw_requirement: str) -> str:
        """Step 0: Ask the model whether the input is actionable enough."""
        print("=" * 60)
        print("[Step 0/9] 校验测试需求是否足够明确...")
        print("=" * 60)

        validation_json = self._call_api(
            system_prompt=INTAKE_VALIDATION_SYSTEM_PROMPT,
            user_message=raw_requirement,
            temperature=0,
            max_tokens=2048,
        )
        self.intake_validation = self._ensure_dict(
            self._parse_json(validation_json),
            "intake_validation",
        )
        status = str(self.intake_validation.get("status", "")).strip().lower()
        reason = self.intake_validation.get("reason", "")
        questions = self.intake_validation.get("questions", [])
        if isinstance(questions, str):
            questions = [questions]

        if status != "ready":
            print("\n需求不明确，暂不继续生成测试计划。")
            if reason:
                print(f"原因: {reason}")
            if questions:
                print("需要补充:")
                for index, question in enumerate(questions, start=1):
                    print(f"{index}. {question}")
            raise RuntimeError("Requirement needs clarification before pipeline generation.")

        normalized = str(self.intake_validation.get("normalized_requirement") or "").strip()
        if not normalized:
            normalized = raw_requirement
        print("\n需求校验通过，继续后续流程。")
        if reason:
            print(f"说明: {reason}")
        return normalized

    # ═══ Step 1: Extract Fields ═══
    def step_extract_fields(self) -> dict:
        """Step 1: Extract structured fields from validated requirement context."""
        print("=" * 60)
        print("[Step 1/9] 提取结构化字段...")
        print("=" * 60)

        fields_json = self._call_api(
            system_prompt=EXTRACT_FIELDS_SYSTEM_PROMPT,
            user_message=f"请从以下测试需求中提取字段：\n\n{self.requirement_text}",
            temperature=0.2,
            max_tokens=2048,
        )
        self.fields = self._ensure_dict(self._parse_json(fields_json), "fields")
        print(json.dumps(self.fields, ensure_ascii=False, indent=2))
        return self.fields

    def step_discover_project_context(self, raw_requirement: str) -> dict:
        """Discover existing project structure before generating plan and cases."""
        print("=" * 60)
        print("[Step 2/9] Discovering existing project context...")
        print("=" * 60)

        project_root = self._select_project_root(raw_requirement)
        if not project_root:
            self.project_context_discovery = {
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
            print("No project root discovered; downstream prompts will require repository inspection.")
            return self.project_context_discovery

        self.project_context_discovery = self._collect_project_context(
            project_root=project_root,
            raw_requirement=raw_requirement,
        )
        print(json.dumps({
            "status": self.project_context_discovery.get("status"),
            "project_root": self.project_context_discovery.get("project_root"),
            "relevant_files": self.project_context_discovery.get("relevant_files", [])[:8],
            "recommended_commands": self.project_context_discovery.get("recommended_commands", []),
        }, ensure_ascii=False, indent=2))
        return self.project_context_discovery

    # ═══ Step 3: Generate Test Plan ═══
    def step_generate_test_plan(self) -> str:
        """Step 3: Generate a human-readable test plan."""
        print("=" * 60)
        print("[Step 3/9] 生成测试方案...")
        print("=" * 60)

        user_prompt = self._build_test_plan_user_prompt(self.fields)
        self.test_plan = self._call_api(
            system_prompt=TEST_PLAN_SYSTEM_PROMPT,
            user_message=user_prompt,
            temperature=PLAN_TEMPERATURE,
            max_tokens=PLAN_MAX_TOKENS,
        )
        print(f"\n{self.test_plan[:500]}...\n")
        return self.test_plan

    # ═══ Step 4: Generate Structured Test Cases ═══
    def step_generate_test_cases(self) -> dict:
        """Step 4: Generate machine-readable and markdown-ready test cases."""
        print("=" * 60)
        print("[Step 4/9] 生成结构化测试用例...")
        print("=" * 60)

        cases_json = self._call_api(
            system_prompt=TEST_CASES_JSON_SYSTEM_PROMPT,
            user_message=self._build_test_cases_user_prompt(),
            temperature=PLAN_TEMPERATURE,
            max_tokens=PLAN_MAX_TOKENS,
        )
        self.test_cases = self._ensure_dict(self._parse_json(cases_json), "test_cases")
        print(f"生成用例数量: {len(self.test_cases.get('cases', []))}")
        return self.test_cases

    # ═══ Step 5: Build Automation Request ═══
    def step_build_automation_request(self) -> dict:
        """Step 5: Generate an automation implementation request, not code."""
        print("=" * 60)
        print("[Step 5/9] 生成自动化脚本实现请求...")
        print("=" * 60)

        request_json = self._call_api(
            system_prompt=AUTOMATION_REQUEST_JSON_SYSTEM_PROMPT,
            user_message=self._build_automation_request_prompt(),
            temperature=0.2,
            max_tokens=4096,
        )
        self.automation_request = self._ensure_dict(
            self._parse_json(request_json),
            "automation_request",
        )
        print(json.dumps(self.automation_request, ensure_ascii=False, indent=2)[:800])
        return self.automation_request

    # ═══ Step 6: Build Execution Request ═══
    def step_build_execution_request(self) -> dict:
        """Step 6: Generate a future execution request, not a test run."""
        print("=" * 60)
        print("[Step 6/9] 生成执行请求...")
        print("=" * 60)

        request_json = self._call_api(
            system_prompt=EXECUTION_REQUEST_JSON_SYSTEM_PROMPT,
            user_message=self._build_execution_request_prompt(),
            temperature=0.2,
            max_tokens=4096,
        )
        self.execution_request = self._ensure_dict(
            self._parse_json(request_json),
            "execution_request",
        )
        print(json.dumps(self.execution_request, ensure_ascii=False, indent=2)[:800])
        return self.execution_request

    # ═══ Step 7: Review Gate ═══
    def step_review_gate(self) -> dict:
        """Step 7: Review generated artifacts before Codex handoff."""
        print("=" * 60)
        print(f"[Step 7/9] 审查生成产物 (policy: {self.review_policy})...")
        print("=" * 60)

        self.review_result = self._build_review_result()
        print(self._build_review_summary())

        if self.review_policy == "ask":
            self._prompt_for_review_confirmation()
        elif self.review_policy == "auto-review" and self.review_result.get("decision") == "blocked":
            print("\n[REVIEW] Auto-review 已阻塞 Codex handoff，将保存审查结果后停止交接生成。")

        return self.review_result

    # ═══ Step 8: Build Codex Handoff ═══
    def step_build_codex_handoff(self) -> dict:
        """Step 8: Build deterministic handoff artifacts for Codex/GPT-5.5."""
        print("=" * 60)
        print("[Step 8/9] 准备后台交接信息...")
        print("=" * 60)

        self.project_context_request = self._build_project_context_request()
        self.codex_task = self._build_codex_task_json()
        print("后台交接信息已准备。")
        return self.codex_task

    # ═══ Step 9: Save Output ═══
    def step_save_output(self, raw_requirement: str,
                         output_dir: str = None,
                         full_artifacts: bool = False) -> str:
        """Step 9: Save artifacts to a readable workbench layout."""
        print("=" * 60)
        print("[Step 9/9] 保存输出文档...")
        print("=" * 60)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        feature_name = self._extract_feature_name(raw_requirement)
        folder_name = f"{feature_name}_{timestamp}"

        base_dir = Path(output_dir) if output_dir else Path.cwd() / "output"
        run_dir = base_dir / folder_name
        raw_dir = run_dir / "raw"
        md_dir = run_dir / "md"
        json_dir = run_dir / "json"
        exports_dir = run_dir / "exports"
        full_dir = run_dir / "full"

        self._write_text(raw_dir / "raw_requirement.txt", raw_requirement)
        self._write_text(md_dir / "requirement.md", self.requirement_text)
        self._write_text(md_dir / "test_plan.md", self._build_test_plan_markdown())
        self._write_text(md_dir / "test_cases.md", self._build_cases_markdown())
        self._write_text(md_dir / "codex_task.md", self._build_codex_task_markdown())
        self._write_text(md_dir / "report.md", self._build_report_markdown(raw_requirement))
        write_test_cases_xlsx(exports_dir / "test_cases.xlsx", self.test_cases)
        write_test_cases_xmind(exports_dir / "test_cases.xmind", self.test_cases)

        self._write_json(json_dir / "automation_request.json", self.automation_request)
        self._write_json(json_dir / "test_cases.json", self.test_cases)
        self._write_json(json_dir / "execution_request.json", self.execution_request)
        self._write_json(json_dir / "review_result.json", self.review_result)
        self._write_text(md_dir / "review_notes.md", self._build_review_notes_markdown())

        if full_artifacts:
            self._write_json(full_dir / "fields.json", self.fields)
            self._write_json(full_dir / "project_context_discovery.json", self.project_context_discovery)
            self._write_text(full_dir / "project_context_discovery.md", self._build_project_context_discovery_markdown())
            self._write_json(full_dir / "review_result.json", self.review_result)
            self._write_text(full_dir / "review_notes.md", self._build_review_notes_markdown())
            self._write_json(full_dir / "project_context_request.json", self.project_context_request)
            self._write_json(full_dir / "codex_task.json", self.codex_task)

        self._write_text(run_dir / "index.html", build_html_viewer(run_dir))

        print(f"\n输出目录: {run_dir}/")
        print("  ├── index.html")
        print("  ├── raw/raw_requirement.txt")
        print("  ├── md/requirement.md")
        print("  ├── md/test_plan.md")
        print("  ├── md/test_cases.md")
        print("  ├── md/report.md")
        print("  ├── exports/test_cases.xlsx")
        print("  ├── exports/test_cases.xmind")
        print("  └── json/ and md/codex_task.md (后台交接产物)")
        if full_artifacts:
            print("  └── full/ (审计和机器交接补充产物)")
        return str(run_dir)

    # ═══ Helpers ═══
    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _extract_feature_name(text: str, max_len: int = 20) -> str:
        """Extract a short feature name from the raw requirement."""
        name = text.strip().split("\n")[0].strip()
        for prefix in ["测试", "请", "帮我", "我要", "需要"]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        name = re.sub(r"[^\u4e00-\u9fff\w]", "_", name)
        return name[:max_len].strip("_") or "test_plan"

    def _select_project_root(self, raw_requirement: str) -> Path | None:
        """Pick the strongest local project root candidate without using the API."""
        candidates = []
        source_text = "\n".join([
            raw_requirement or "",
            self.requirement_text or "",
            json.dumps(self.fields, ensure_ascii=False),
        ])

        candidates.extend(self._extract_path_candidates(source_text))

        cwd = Path.cwd()
        for base in [cwd, *cwd.parents]:
            candidates.append(base / "auto-test")
            candidates.append(base)

        seen = set()
        scored = []
        for candidate in candidates:
            root = self._normalize_project_root(candidate)
            if not root:
                continue
            key = str(root).lower()
            if key in seen:
                continue
            seen.add(key)
            score = self._score_project_root(root)
            if score > 0:
                scored.append((score, root))

        if not scored:
            return None
        scored.sort(key=lambda item: (item[0], len(str(item[1]))), reverse=True)
        return scored[0][1]

    def _extract_path_candidates(self, text: str) -> list[Path]:
        """Extract Windows path candidates and trim natural-language suffixes."""
        candidates = []
        for match in re.finditer(r"[A-Za-z]:\\[^\r\n\"'<>|]+", text or ""):
            raw_candidate = match.group(0).strip().rstrip(".,;:，。；：)）]")
            resolved = self._longest_existing_path_prefix(raw_candidate)
            if resolved:
                candidates.append(resolved)
            else:
                candidates.append(Path(raw_candidate))
        return candidates

    @staticmethod
    def _longest_existing_path_prefix(raw_path: str) -> Path | None:
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

    @staticmethod
    def _normalize_project_root(path: Path) -> Path | None:
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

    @staticmethod
    def _score_project_root(root: Path) -> int:
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

    def _collect_project_context(self, project_root: Path, raw_requirement: str) -> dict:
        terms = self._derive_discovery_terms(raw_requirement)
        framework_signals = self._discover_framework_signals(project_root)
        relevant_files = self._find_relevant_files(project_root, terms)
        snippets = [
            self._build_file_snippet(project_root / item["path"], terms)
            for item in relevant_files[:10]
        ]
        snippets = [item for item in snippets if item]
        recommended_commands = self._infer_recommended_commands(project_root, relevant_files)
        hard_constraints, forbidden_patterns, notes = self._infer_project_constraints(
            project_root=project_root,
            relevant_files=relevant_files,
            terms=terms,
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
        }

    def _derive_discovery_terms(self, raw_requirement: str) -> list[str]:
        source = "\n".join([
            raw_requirement or "",
            self.requirement_text or "",
            json.dumps(self.fields, ensure_ascii=False),
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

    @staticmethod
    def _discover_framework_signals(project_root: Path) -> list[dict]:
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

    def _find_relevant_files(self, project_root: Path, terms: list[str]) -> list[dict]:
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

        for path in self._iter_project_files(project_root):
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
            if score == 0:
                score += self._score_file_content(path, terms, matched)
            else:
                score += self._score_file_content(path, terms, matched)
            if score:
                scored_files.append({
                    "path": str(relative).replace("\\", "/"),
                    "score": score,
                    "matched_terms": sorted(matched)[:12],
                })

        scored_files.sort(key=lambda item: (item["score"], -len(item["path"])), reverse=True)
        return scored_files

    @staticmethod
    def _iter_project_files(project_root: Path):
        try:
            for path in project_root.rglob("*"):
                if path.is_file():
                    yield path
        except OSError:
            return

    @staticmethod
    def _score_file_content(path: Path, terms: list[str], matched: set) -> int:
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

    @staticmethod
    def _build_file_snippet(path: Path, terms: list[str]) -> dict | None:
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

    @staticmethod
    def _infer_recommended_commands(project_root: Path, relevant_files: list[dict]) -> list[str]:
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

    @staticmethod
    def _infer_project_constraints(project_root: Path, relevant_files: list[dict],
                                   terms: list[str]) -> tuple[list[str], list[str], list[str]]:
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

    def _project_context_prompt(self) -> str:
        if not self.project_context_discovery:
            return "Project context discovery has not run. Do not invent project files or commands."
        return f"""Project context discovery has already inspected the local repository before this artifact was generated.
You must obey the discovered project_root, framework_signals, relevant_files, recommended_commands, and hard_constraints.
If a requested file/class/fixture/command conflicts with discovery, prefer discovery and add a need-confirmation item.
Do not invent files, classes, selectors, fixtures, or execution commands that conflict with this context.

{self._json_block(self.project_context_discovery)}"""

    def _build_project_context_discovery_markdown(self) -> str:
        context = self.project_context_discovery or {}
        lines = [
            "# Project Context Discovery",
            "",
            f"- Status: {context.get('status', 'not_run')}",
            f"- Project root: {context.get('project_root') or 'not discovered'}",
            "",
            "## Framework Signals",
        ]
        signals = context.get("framework_signals", [])
        lines.extend(self._markdown_bullets(
            f"{item.get('path')} ({item.get('type')})" for item in signals
        ))
        lines.extend(["", "## Relevant Files"])
        lines.extend(self._markdown_bullets(
            f"{item.get('path')} - score {item.get('score')} - matched: {', '.join(item.get('matched_terms', []))}"
            for item in context.get("relevant_files", [])
        ))
        lines.extend(["", "## Recommended Commands"])
        lines.extend(self._markdown_bullets(context.get("recommended_commands", [])))
        lines.extend(["", "## Hard Constraints"])
        lines.extend(self._markdown_bullets(context.get("hard_constraints", [])))
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

    @staticmethod
    def _markdown_bullets(items) -> list[str]:
        values = [str(item) for item in items if item]
        if not values:
            return ["- None"]
        return [f"- {item}" for item in values]

    @staticmethod
    def _parse_json(text: str):
        """Parse JSON from LLM output, handling markdown code blocks."""
        text = text.strip()
        text = re.sub(r"^```(?:json)?\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            object_match = re.search(r"\{.*\}", text, re.DOTALL)
            if object_match:
                return json.loads(object_match.group())
            array_match = re.search(r"\[.*\]", text, re.DOTALL)
            if array_match:
                return json.loads(array_match.group())
            raise

    @staticmethod
    def _ensure_dict(payload, fallback_key: str) -> dict:
        if isinstance(payload, dict):
            return payload
        if isinstance(payload, list):
            return {fallback_key: payload}
        raise TypeError(f"Expected JSON object or array, got {type(payload).__name__}")

    @staticmethod
    def _fmt(val, default: str = "待补充") -> str:
        if not val:
            return default
        if isinstance(val, list):
            return "\n".join(f"{i + 1}. {item}" for i, item in enumerate(val))
        return str(val)

    @staticmethod
    def _json_block(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _cell(value) -> str:
        if isinstance(value, list):
            value = "<br>".join(str(item) for item in value)
        elif isinstance(value, dict):
            value = "<br>".join(f"{key}: {val}" for key, val in value.items())
        elif value is None:
            value = ""
        return str(value).replace("\n", "<br>").replace("|", "\\|")

    def _build_test_plan_user_prompt(self, fields: dict) -> str:
        """Build a project-aware user prompt for test plan generation."""
        return f"""请根据以下信息生成测试方案：

【项目上下文发现（必须遵守）】
{self._project_context_prompt()}

【测试对象】{self._fmt(fields.get('test_object'))}

【业务背景】{self._fmt(fields.get('business_context'))}

【核心需求】{self._fmt(fields.get('core_requirements'))}

【用户角色】{self._fmt(fields.get('user_roles'))}

【输入条件】{self._fmt(fields.get('input_conditions'))}

【预期结果】{self._fmt(fields.get('expected_results'))}

【异常场景】{self._fmt(fields.get('exception_scenarios'))}

要求：
1. 项目上下文发现是文件结构、框架、已有测试逻辑和执行命令的事实来源。
2. 测试方案必须复用已有测试结构，不能臆造与项目上下文冲突的页面对象、选择器、fixture、测试文件或命令。
3. 如果输入材料已经明确给出测试点或测试用例，只围绕这些已明确内容输出；用户只指定其中一个功能点时，不要展开同一材料中的其他功能点。
4. 如果需求与已有代码逻辑存在不确定点，请写入待确认问题，不要把假设写成确定预期。"""

    def _build_test_cases_user_prompt(self) -> str:
        """Build a project-aware user prompt for structured test cases."""
        return f"""请基于以下结构化字段、项目上下文发现和测试方案生成结构化测试用例 JSON。

【项目上下文发现（必须遵守）】
{self._project_context_prompt()}

【结构化字段】
{self._json_block(self.fields)}

【测试方案】
{self.test_plan}

要求：
1. 用例预期必须以项目上下文和已有代码逻辑为准。
2. 不要因为常见直觉推翻已有测试逻辑；如果与直觉冲突，把原因写入 assumptions 或 need_confirmation。
3. 如果输入材料已经明确给出测试点或测试用例，只生成这些已明确范围内的用例；不要扩展未指定功能点。
4. 不要输出要求新增未知测试文件、未知页面对象、未知 fixture 的用例前置条件。"""

    def _build_automation_request_prompt(self) -> str:
        """Build a project-aware automation handoff prompt."""
        return f"""请基于以下信息生成自动化脚本实现请求 JSON。注意：只生成请求，不输出代码。

【项目上下文发现（必须遵守）】
{self._project_context_prompt()}

【结构化字段】
{self._json_block(self.fields)}

【测试方案】
{self.test_plan}

【结构化测试用例】
{self._json_block(self.test_cases)}

要求：
1. suggested_changes 必须优先指向已发现的现有文件。
2. 除非项目上下文明确没有可复用位置，否则不要建议新建测试文件、页面对象、选择器文件或 fixture。
3. 不要建议与 project_context_discovery.hard_constraints 或 forbidden_patterns 冲突的内容。"""

    def _build_execution_request_prompt(self) -> str:
        """Build a project-aware execution handoff prompt."""
        return f"""请基于以下信息生成测试执行请求 JSON。注意：只生成执行计划，不真正执行测试。

【项目上下文发现（必须遵守）】
{self._project_context_prompt()}

【结构化字段】
{self._json_block(self.fields)}

【结构化测试用例】
{self._json_block(self.test_cases)}

【自动化脚本实现请求】
{self._json_block(self.automation_request)}

要求：
1. 优先使用 project_context_discovery.recommended_commands。
2. 不要输出通用的 pytest tests/、npm test 等命令，除非项目上下文明确支持。
3. 如果执行前需要登录账号、浏览器进程、环境变量或测试数据，写入 pre_run_checks。
4. 如果 automation_request.element_evidence_required 为 true，将“确认已完成 CDP/F12 元素证据采集”写入 pre_run_checks。"""

    def _build_review_result(self) -> dict:
        """Review artifacts with deterministic risk rules before Codex handoff."""
        findings = []
        cases = self.test_cases.get("cases", [])
        selected_cases = self.automation_request.get("selected_cases", [])
        context_needed = self.automation_request.get("project_context_needed", [])
        required_environment = self.execution_request.get("required_environment", [])
        pre_run_checks = self.execution_request.get("pre_run_checks", [])

        if isinstance(selected_cases, str):
            selected_cases = [selected_cases]
        if isinstance(context_needed, str):
            context_needed = [context_needed]

        generated_text = json.dumps({
            "test_cases": self.test_cases,
            "automation_request": self.automation_request,
            "execution_request": self.execution_request,
        }, ensure_ascii=False)
        for pattern in self.project_context_discovery.get("forbidden_patterns", []):
            if pattern and pattern.lower() in generated_text.lower():
                findings.append(self._finding(
                    "high",
                    "project_context_conflict",
                    f"Generated artifact conflicts with project discovery forbidden pattern: {pattern}",
                    "Regenerate or edit the artifact so it follows project_context_discovery.hard_constraints and existing project structure.",
                ))

        if self.project_context_discovery.get("status") == "discovered":
            command_values = []
            for command_field in ("commands", "command_candidates"):
                value = self.execution_request.get(command_field, [])
                if isinstance(value, str):
                    command_values.append(value)
                elif isinstance(value, list):
                    command_values.extend(str(item) for item in value)
            commands_text = "\n".join(command_values)
            if "pytest tests/" in commands_text.replace("\\", "/"):
                findings.append(self._finding(
                    "high",
                    "execution_command_conflict",
                    "Execution request uses a generic pytest tests/ command even though project discovery found a concrete project layout.",
                    "Use project_context_discovery.recommended_commands or an explicitly discovered project test path.",
                ))

        if len(cases) > 12:
            findings.append(self._finding(
                "medium",
                "case_scope",
                f"结构化测试用例数量为 {len(cases)}，可能超过首批自动化落地范围。",
                "建议人工删减或调整 P0/P1 自动化候选用例。",
            ))

        if len(selected_cases) > 8:
            findings.append(self._finding(
                "medium",
                "automation_scope",
                f"首批自动化候选用例数量为 {len(selected_cases)}，可能导致一次落地范围过大。",
                "建议优先保留核心 P0 和少量关键 P1。",
            ))

        if len(context_needed) > 8:
            findings.append(self._finding(
                "medium",
                "project_context",
                "自动化实现请求需要大量项目上下文，说明当前上下文不够确定。",
                "Codex 写代码前应先做项目发现，并输出修改计划。",
            ))

        recommended_framework = str(self.automation_request.get("recommended_framework", ""))
        if recommended_framework and recommended_framework not in ("existing_project_framework", "待项目发现"):
            findings.append(self._finding(
                "medium",
                "framework_choice",
                f"自动化请求推荐了框架：{recommended_framework}",
                "如果项目已有测试框架，应优先使用已有框架。",
            ))

        target_type = str(self.automation_request.get("target_type", "unknown"))
        if target_type == "unknown":
            findings.append(self._finding(
                "high",
                "target_type",
                "自动化目标类型仍为 unknown。",
                "Codex handoff 前应确认这是 Web UI、API、单元测试还是集成测试。",
            ))
        elif target_type == "web_ui" and not self.automation_request.get("element_evidence_required"):
            findings.append(self._finding(
                "medium",
                "element_evidence",
                "Web UI 自动化请求未显式声明 element_evidence_required。",
                "Codex 写选择器、页面对象或 UI 流程前必须先采集或请求 CDP/F12 元素证据。",
            ))

        all_safety_text = "\n".join(
            str(item) for item in pre_run_checks + required_environment
        )
        safety_keywords = ["生产", "删除", "支付", "发消息", "改权限", "数据库连接", "测试数据库", "账号", "token", "Token"]
        matched_keywords = [word for word in safety_keywords if word in all_safety_text]
        if matched_keywords:
            findings.append(self._finding(
                "high",
                "environment_safety",
                f"执行请求涉及环境/数据安全敏感项：{', '.join(sorted(set(matched_keywords)))}",
                "进入 Codex 代码落地或执行测试前，需要人工确认测试环境和测试数据安全。",
            ))

        assumptions = self.test_cases.get("assumptions", [])
        if assumptions:
            findings.append(self._finding(
                "low",
                "assumptions",
                f"测试用例包含 {len(assumptions) if isinstance(assumptions, list) else 1} 条假设。",
                "建议在真实代码落地前快速确认这些假设是否成立。",
            ))

        high_count = sum(1 for item in findings if item["severity"] == "high")
        medium_count = sum(1 for item in findings if item["severity"] == "medium")
        if high_count:
            decision = "blocked"
            summary = "发现高风险项，Codex handoff 前需要人工确认。"
        elif medium_count:
            decision = "needs_attention"
            summary = "发现中风险项，auto-review 可继续，但建议人工查看审查说明。"
        else:
            decision = "pass"
            summary = "未发现阻塞项。"

        return {
            "policy": self.review_policy,
            "decision": decision,
            "summary": summary,
            "counts": {
                "high": high_count,
                "medium": medium_count,
                "low": sum(1 for item in findings if item["severity"] == "low"),
            },
            "findings": findings,
            "gate": {
                "codex_handoff_allowed": decision != "blocked" or self.review_policy == "full-auto",
                "requires_user_confirmation": decision == "blocked" or self.review_policy == "ask",
            },
        }

    @staticmethod
    def _finding(severity: str, category: str, message: str, recommendation: str) -> dict:
        return {
            "severity": severity,
            "category": category,
            "message": message,
            "recommendation": recommendation,
        }

    def _build_review_summary(self) -> str:
        if not self.review_result:
            return "[REVIEW] 尚未生成审查结果。"
        counts = self.review_result.get("counts", {})
        return (
            f"[REVIEW] decision={self.review_result.get('decision')} | "
            f"high={counts.get('high', 0)}, medium={counts.get('medium', 0)}, "
            f"low={counts.get('low', 0)}\n"
            f"[REVIEW] {self.review_result.get('summary')}"
        )

    def _prompt_for_review_confirmation(self) -> None:
        answer = input("\n审查完成。是否允许继续生成 Codex handoff？输入 yes/继续 确认：").strip().lower()
        if answer in ("yes", "y", "继续", "确认", "confirm"):
            self.review_result["user_confirmation"] = "confirmed"
            self.review_result["gate"]["codex_handoff_allowed"] = True
            return
        self.review_result["user_confirmation"] = "rejected"
        self.review_result["decision"] = "blocked"
        self.review_result["gate"]["codex_handoff_allowed"] = False

    def _should_build_codex_handoff(self) -> bool:
        if self.review_policy == "full-auto":
            return True
        gate = self.review_result.get("gate", {})
        return bool(gate.get("codex_handoff_allowed"))

    def _set_skipped_codex_handoff(self) -> None:
        self.project_context_request = {
            "status": "skipped_by_review_gate",
            "reason": self.review_result.get("summary", "Review gate blocked handoff."),
        }
        self.codex_task = {
            "phase": "Phase 2.6 Codex handoff",
            "status": "skipped_by_review_gate",
            "reason": self.review_result.get("summary", "Review gate blocked handoff."),
            "review_result": "full/review_result.json",
            "review_notes": "full/review_notes.md",
        }

    def _build_project_context_request(self) -> dict:
        """Build a deterministic request describing what Codex should inspect."""
        target_type = self.automation_request.get("target_type", "unknown")
        context_needed = self.automation_request.get("project_context_needed", [])
        if isinstance(context_needed, str):
            context_needed = [context_needed]

        return {
            "status": "pending_codex_project_discovery",
            "target_type": target_type,
            "purpose": "Guide Codex before it proposes any test-code changes.",
            "must_read_first": [
                "AGENTS.md or project-level agent instructions",
                "README.md or project testing documentation",
                "existing test runner configuration",
                "nearby existing page objects, selectors, fixtures, and testcases",
            ],
            "framework_signals": [
                "pytest.ini",
                "pyproject.toml",
                "conftest.py",
                "package.json",
                "playwright.config.*",
                "cypress.config.*",
                "pom.xml",
                "build.gradle",
            ],
            "candidate_paths": [
                "project/**/pages/**",
                "project/**/selectors/**",
                "project/**/testcases/**",
                "tests/**",
                "e2e/**",
                "specs/**",
            ],
            "discovery_questions": [
                "当前项目使用什么测试框架和运行命令？",
                "目标模块是否已有 page object、selector 或 testcase？",
                "测试账号、环境地址和测试数据是否安全可用？",
                "是否存在状态写入、删除、支付、发消息或权限变更风险？",
            ],
            "additional_context_needed": context_needed,
        }

    def _build_codex_task_json(self) -> dict:
        """Build a machine-readable handoff task for Codex/GPT-5.5."""
        selected_cases = self.automation_request.get("selected_cases", [])
        if isinstance(selected_cases, str):
            selected_cases = [selected_cases]

        return {
            "phase": "Phase 2.6 Codex handoff",
            "status": "ready_for_codex_review",
            "owner_split": {
                "deepseek": [
                    "intake 校验",
                    "字段抽取",
                    "测试方案生成",
                    "测试用例结构化",
                    "自动化实现请求生成",
                    "执行请求生成",
                ],
                "codex_gpt_55": [
                    "读取项目上下文",
                    "为 Web UI 元素采集或请求 CDP/F12 证据",
                    "制定代码修改计划",
                    "等待用户确认",
                    "修改自动化测试代码",
                    "运行测试并修复脚本问题",
                    "输出执行报告",
                ],
            },
            "source_artifacts": {
                "raw_requirement": "raw/raw_requirement.txt",
                "requirement": "md/requirement.md",
                "test_plan": "md/test_plan.md",
                "test_cases_markdown": "md/test_cases.md",
                "test_cases_json": "json/test_cases.json",
                "automation_request": "json/automation_request.json",
                "execution_request": "json/execution_request.json",
                "report": "md/report.md",
                "full_artifacts": "full/ (only when --full-artifacts is used)",
            },
            "selected_cases": selected_cases,
            "project_context_discovery": self.project_context_discovery,
            "project_context_request": self.project_context_request,
            "element_evidence_gate": {
                "required_for_web_ui": True,
                "source": "CDP, browser inspection, or user-provided DevTools/F12 DOM",
                "must_collect_before": [
                    "新增或修改 selector",
                    "新增或修改 page object 操作",
                    "新增或修改 UI 点击/读取/断言流程",
                ],
                "must_present": [
                    "元素用途",
                    "最小 DOM/outerHTML 证据",
                    "稳定属性",
                    "点击/选择/保存前后的状态变化",
                    "最终选择器和选择原因",
                ],
                "blocked_without_evidence": True,
            },
            "confirmation_gate": {
                "required": True,
                "before_actions": [
                    "修改测试代码",
                    "修改选择器",
                    "修改页面对象",
                    "修改测试流程",
                    "运行可能改变业务数据的测试",
                ],
                "codex_must_present": [
                    "准备修改的文件",
                    "每个文件的修改点",
                    "修改原因",
                    "预计验证命令",
                    "环境和数据安全判断",
                    "UI 元素证据状态（如涉及 Web UI）",
                ],
            },
            "implementation_constraints": [
                "优先复用已有 page object、selector、fixture 和 testcase 结构。",
                "新增选择器优先补充到已有 selector 类中。",
                "新增测试用例放到项目既有 testcases 目录中。",
                "测试用例只编排流程和断言，页面细节放到 page object。",
                "涉及 Web UI 元素时，未采集真实 DOM/状态变化证据前不得猜测 selector 或添加兜底逻辑。",
                "不引入新测试框架，除非项目没有合适框架且用户确认。",
            ],
            "next_agent_prompt_file": "md/codex_task.md",
        }

    def _build_codex_task_markdown(self) -> str:
        """Build the human-readable Codex handoff prompt."""
        if self.codex_task.get("status") == "skipped_by_review_gate":
            return f"""# Codex Handoff Task

Codex handoff was skipped by the review gate.

## 原因

{self.codex_task.get('reason', 'Review gate blocked handoff.')}

## 下一步

1. 读取 `md/report.md`。
2. 根据审查建议确认或调整 `json/test_cases.json`、`json/automation_request.json`、`json/execution_request.json`。
3. 如使用了 `--full-artifacts`，可读取 `full/review_notes.md` 和 `full/review_result.json` 查看更完整审查信息。
4. 如确认可以继续，再使用 `--review-policy ask` 或 `--review-policy full-auto` 重新运行。
"""

        return f"""# Codex Handoff Task

{CODEX_HANDOFF_REQUIREMENTS}

## 任务目标

请基于本目录中的 Phase 2 产物接管自动化测试代码落地。DeepSeek 已完成测试设计和结构化分析；Codex/GPT-5.5 负责读取项目、提出修改计划、等待用户确认、修改代码、运行测试并修复脚本问题。

## 必读产物

- `md/requirement.md`
- `md/test_plan.md`
- `md/test_cases.md`
- `md/report.md`
- `json/test_cases.json`
- `json/automation_request.json`
- `json/execution_request.json`
- `raw/raw_requirement.txt`

## 修改前确认 Gate

在任何代码修改前，必须先向用户说明：

1. 准备修改的文件
2. 每个文件的修改点
3. 修改原因
4. 预计运行的验证命令
5. 环境和测试数据安全判断

只有用户明确说“可以修改”“确认修改”“按这个改”后，才能修改代码文件。

## CDP 元素证据 Gate

如果本任务涉及 Web UI 元素定位、点击、读取或断言，在输出代码修改计划前必须先完成或请求元素证据采集：

1. 目标元素用途
2. 最小 DOM/outerHTML 片段
3. 稳定属性（如 id、name、value、role、aria-*、data-*、checked、selected、disabled、稳定 class）
4. 点击、选择、保存或展开前后的状态变化
5. 最终选择器和选择原因

没有真实 DOM 和状态变化证据时，不得猜测选择器、隐藏 input、class 状态、可点击祖先或兜底选择器。

## DeepSeek 与 Codex 职责边界

- DeepSeek 负责：需求 intake 校验、字段抽取、测试方案、结构化用例、自动化实现请求、执行请求。
- Codex 负责：项目上下文读取、代码修改计划、自动化测试代码落地、测试执行、失败修复、最终报告。

## 当前测试设计摘要

### 结构化字段

```json
{self._json_block(self.fields)}
```

### 自动化实现请求

```json
{self._json_block(self.automation_request)}
```

### 执行请求

```json
{self._json_block(self.execution_request)}
```

## 下一步建议

1. 读取 `md/test_plan.md`、`md/test_cases.md`、`json/automation_request.json` 和 `json/execution_request.json`。
2. 在目标项目中执行只读发现，复核测试框架、目录结构、页面对象、选择器、fixtures 和运行命令。
3. 基于 `json/test_cases.json` 选择首批 P0/P1 自动化候选用例。
4. 如果运行时存在 `full/` 目录，可读取其中的 project context 和 review 补充产物。
5. 如涉及 Web UI，先采集或请求 CDP/F12 元素证据，并输出元素证据表。
6. 输出代码修改计划并等待用户确认。
"""

    def _build_review_notes_markdown(self) -> str:
        """Build a human-readable review report."""
        if not self.review_result:
            return "# Auto Test Flow Review\n\n尚未生成审查结果。\n"

        counts = self.review_result.get("counts", {})
        lines = [
            "# Auto Test Flow Review",
            "",
            REVIEW_POLICY_GUIDE,
            "",
            "## 审查结论",
            "",
            f"- Policy: `{self.review_result.get('policy')}`",
            f"- Decision: `{self.review_result.get('decision')}`",
            f"- Summary: {self.review_result.get('summary')}",
            "",
            "## 风险统计",
            "",
            f"- High: {counts.get('high', 0)}",
            f"- Medium: {counts.get('medium', 0)}",
            f"- Low: {counts.get('low', 0)}",
            "",
            "## 发现项",
            "",
        ]

        findings = self.review_result.get("findings", [])
        if not findings:
            lines.append("- 暂无")
        else:
            for item in findings:
                lines.extend([
                    f"### {item.get('severity', '').upper()} - {item.get('category', '')}",
                    "",
                    f"- 问题: {item.get('message', '')}",
                    f"- 建议: {item.get('recommendation', '')}",
                    "",
                ])

        gate = self.review_result.get("gate", {})
        lines.extend([
            "## Gate 状态",
            "",
            f"- Codex handoff allowed: `{gate.get('codex_handoff_allowed')}`",
            f"- Requires user confirmation: `{gate.get('requires_user_confirmation')}`",
            "",
        ])
        return "\n".join(lines)

    def _build_test_plan_markdown(self) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""# 自动化测试方案

> 生成时间: {timestamp}
> 模型: {MODEL}

---

## 结构化字段

```json
{self._json_block(self.fields)}
```

---

## 测试方案

{self.test_plan}
"""

    def _build_cases_markdown(self) -> str:
        cases = self.test_cases.get("cases", [])
        assumptions = self.test_cases.get("assumptions", [])
        need_confirmation = self.test_cases.get("need_confirmation", [])

        lines = [
            "# 自动化测试用例",
            "",
            "## 合理假设",
            "",
        ]
        lines.extend(self._markdown_list(assumptions))
        lines.extend(["", "## 测试用例表", ""])
        lines.append("| 用例编号 | 用例标题 | 优先级 | 类型 | 自动化候选 | 前置条件 | 测试步骤 | 测试数据 | 预期结果 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")

        for case in cases:
            lines.append(
                "| {id} | {title} | {priority} | {type} | {automation_candidate} | "
                "{preconditions} | {steps} | {test_data} | {expected_result} |".format(
                    id=self._cell(case.get("id")),
                    title=self._cell(case.get("title")),
                    priority=self._cell(case.get("priority")),
                    type=self._cell(case.get("type")),
                    automation_candidate=self._cell(case.get("automation_candidate")),
                    preconditions=self._cell(case.get("preconditions")),
                    steps=self._cell(case.get("steps")),
                    test_data=self._cell(case.get("test_data")),
                    expected_result=self._cell(case.get("expected_result")),
                )
            )

        lines.extend(["", "## 需要确认的问题", ""])
        lines.extend(self._markdown_list(need_confirmation))
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _markdown_list(items) -> list:
        if not items:
            return ["- 暂无"]
        if isinstance(items, str):
            return [f"- {items}"]
        return [f"- {item}" for item in items]

    def _build_report_markdown(self, raw_requirement: str) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"""# 自动化测试工作流报告

> 生成时间: {timestamp}
> 模型: {MODEL}
> 阶段: Phase 2.6 pipeline with review policy and Codex handoff

## 原始需求

{raw_requirement}

## 已完成

- 已保存原始需求和校验后的需求上下文。
- 已抽取结构化字段。
- 已生成测试方案。
- 已生成测试用例，并导出 Markdown、Excel、XMind 三种查看格式。
- 已在后台准备自动化实现请求、执行请求和 Codex 交接材料，用于后续无感执行。
- 已完成自动审查；如使用 `--full-artifacts`，会额外保存审计补充产物。

## 产物清单

- `index.html`
- `raw/raw_requirement.txt`
- `md/requirement.md`
- `md/test_plan.md`
- `md/test_cases.md`
- `md/report.md`
- `exports/test_cases.xlsx`
- `exports/test_cases.xmind`

## 当前边界

- 本阶段不直接修改被测项目代码。
- 本阶段不真实执行测试。
- 自动化代码生成、项目发现、测试执行和失败修复由后台 Codex 交接阶段接入。
- Codex 修改任何代码前仍必须先输出修改计划并等待用户确认。

## 需要确认的问题

{self._format_report_questions()}
"""

    def _format_report_questions(self) -> str:
        questions = []
        for payload in (self.test_cases, self.automation_request, self.execution_request):
            values = payload.get("need_confirmation", []) if isinstance(payload, dict) else []
            if isinstance(values, str):
                questions.append(values)
            else:
                questions.extend(values)
        if not questions:
            return "- 暂无"
        seen = set()
        unique = []
        for question in questions:
            if question not in seen:
                seen.add(question)
                unique.append(question)
        return "\n".join(f"- {question}" for question in unique)

    # ═══ Main Pipeline ═══
    def run(self, raw_requirement: str, output_dir: str = None,
            review_policy: str = "auto-review",
            full_artifacts: bool = False, serve: bool = False,
            port: int = 8765) -> str:
        """Execute the full Phase 2.6 pipeline."""
        if review_policy not in REVIEW_POLICIES:
            raise ValueError(f"Unsupported review policy: {review_policy}")
        self.review_policy = review_policy
        self.full_artifacts = full_artifacts

        print(f"\n{'=' * 60}")
        print("  自动化测试工作流 (Phase 2.6)")
        print(f"  审查策略: {self.review_policy}")
        print(f"  全量产物: {'是' if full_artifacts else '否'}")
        print(f"  输入: {raw_requirement[:50]}{'...' if len(raw_requirement) > 50 else ''}")
        print(f"{'=' * 60}\n")

        try:
            original_requirement = raw_requirement
            self.requirement_text = self.step_validate_intake(raw_requirement)

            self.step_extract_fields()
            self.step_discover_project_context(original_requirement)
            self.step_generate_test_plan()
            self.step_generate_test_cases()
            self.step_build_automation_request()
            self.step_build_execution_request()
            self.step_review_gate()
            if self._should_build_codex_handoff():
                self.step_build_codex_handoff()
            else:
                self._set_skipped_codex_handoff()
            result_dir = self.step_save_output(original_requirement, output_dir, full_artifacts=full_artifacts)

            print(f"\n{'=' * 60}")
            print("  工作流完成!")
            print(f"{'=' * 60}")
            if serve:
                self._serve_output(Path(result_dir), port)
            return result_dir

        except Exception as e:
            print(f"\n[ERROR] 工作流执行失败: {e}", file=sys.stderr)
            raise

    @staticmethod
    def _serve_output(run_dir: Path, port: int) -> None:
        """Serve a generated output folder as a local read-only workbench."""
        handler = functools.partial(
            http.server.SimpleHTTPRequestHandler,
            directory=str(run_dir),
        )
        with socketserver.TCPServer(("127.0.0.1", port), handler) as httpd:
            url = f"http://127.0.0.1:{port}/"
            print(f"\n本地查看服务已启动: {url}")
            print("按 Ctrl+C 停止服务。")
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\n本地查看服务已停止。")


def main():
    parser = argparse.ArgumentParser(
        description="自动化测试工作流编排器 - Phase 2.6",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python orchestrator.py "对登录页面进行功能测试"
  python orchestrator.py --file requirements.txt
  python orchestrator.py "测试注册功能" --output-dir ./output
  python orchestrator.py "测试登录功能" --review-policy ask
  python orchestrator.py "测试登录功能" --serve --port 8765
  python orchestrator.py "测试登录功能" --full-artifacts
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("requirement", nargs="?", help="测试需求描述")
    group.add_argument("-f", "--file", help="从文件读取测试需求")

    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出根目录(可选，默认当前目录下的 output/)")
    parser.add_argument("--review-policy", choices=REVIEW_POLICIES,
                        default="auto-review",
                        help="审查策略: ask=命令行确认, auto-review=自动审查并阻塞高风险, full-auto=不阻塞")
    parser.add_argument("--full-artifacts", action="store_true",
                        help="额外生成 full/ 审计和机器交接补充产物")
    parser.add_argument("--serve", action="store_true",
                        help="生成完成后启动本地 HTTP 服务查看 index.html")
    parser.add_argument("--port", type=int, default=8765,
                        help="--serve 使用的本地端口，默认 8765")

    args = parser.parse_args()

    if args.file:
        requirement = Path(args.file).read_text(encoding="utf-8")
    else:
        requirement = args.requirement

    if not requirement or not requirement.strip():
        parser.print_help()
        sys.exit(1)

    orchestrator = TestWorkflowOrchestrator()
    orchestrator.run(
        raw_requirement=requirement.strip(),
        output_dir=args.output_dir,
        review_policy=args.review_policy,
        full_artifacts=args.full_artifacts,
        serve=args.serve,
        port=args.port,
    )


if __name__ == "__main__":
    main()
