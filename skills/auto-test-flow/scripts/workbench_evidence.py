#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Page evidence capture and diff helpers for the auto-test-flow workbench."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from element_evidence import format_evidence_table, scan_page
from workbench_cdp import DEFAULT_ELECTRON_DEBUG_PORT, DEFAULT_STORE_DEBUG_PORT


EVIDENCE_MODES = {"url", "electron_cdp", "store_cdp"}


def start_evidence_job(state, payload: dict, apply_settings_func) -> dict:
    apply_settings_func(state, payload)
    mode = str(payload.get("mode", "url")).strip() or "url"
    if mode not in EVIDENCE_MODES:
        raise ValueError(f"mode must be one of {sorted(EVIDENCE_MODES)}")
    target_url = str(payload.get("target_url", "")).strip()
    selector_filter = str(payload.get("selector_filter", "")).strip() or None
    run_ref = str(payload.get("run_dir", "")).strip()
    run_dir = state.resolve_run_dir(run_ref) if run_ref else create_evidence_run_dir(state, target_url)
    cdp_port = int(payload.get("cdp_port") or (DEFAULT_STORE_DEBUG_PORT if mode == "store_cdp" else DEFAULT_ELECTRON_DEBUG_PORT))
    browser_channel = str(payload.get("browser_channel", "chrome")).strip() or "chrome"

    job = state.create_job("evidence")
    job_id = job["id"]
    thread = threading.Thread(
        target=_run_evidence_job,
        args=(state, job_id, run_dir, mode, target_url, selector_filter, cdp_port, browser_channel),
        daemon=True,
    )
    thread.start()
    return job


def create_evidence_run_dir(state, target_url: str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    label = _safe_evidence_label(target_url) if target_url else "evidence"
    run_dir = state.output_dir / f"{label}_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_evidence_index(run_dir)
    return run_dir


def _safe_evidence_label(target_url: str) -> str:
    label = re.sub(r"^https?://", "", target_url.strip(), flags=re.IGNORECASE)
    label = re.sub(r"[^\w.-]+", "_", label).strip("._")
    return (label[:32] or "evidence").strip("_") or "evidence"


def _write_evidence_index(run_dir: Path) -> None:
    md_dir = run_dir / "md"
    json_dir = run_dir / "json"
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    index = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Evidence Capture</title>
  <style>
    body{font-family:Arial,"Microsoft YaHei",sans-serif;margin:32px;color:#0f172a;background:#f8fafc}
    a{display:block;margin:12px 0;color:#2563eb}
  </style>
</head>
<body>
  <h1>页面证据采集</h1>
  <a href="md/evidence.md">evidence.md</a>
  <a href="json/evidence.json">evidence.json</a>
  <a href="md/evidence_diff.md">evidence_diff.md</a>
</body>
</html>
"""
    (run_dir / "index.html").write_text(index, encoding="utf-8")


def _run_evidence_job(
    state,
    job_id: str,
    run_dir: Path,
    mode: str,
    target_url: str,
    selector_filter: str | None,
    cdp_port: int,
    browser_channel: str,
) -> None:
    log_path = run_dir / "logs" / f"evidence_{job_id}.log"
    try:
        state.update_job(
            job_id,
            run_dir=str(run_dir),
            run_url=f"/runs/{quote(run_dir.name)}/index.html",
            log_file=str(log_path),
        )
        _append_standalone_log(state, job_id, log_path, f"Evidence mode: {mode}")
        if target_url:
            _append_standalone_log(state, job_id, log_path, f"Target URL: {target_url}")
        payload = _capture_evidence_payload(
            mode=mode,
            target_url=target_url,
            selector_filter=selector_filter,
            cdp_port=cdp_port,
            browser_channel=browser_channel,
        )
        _write_evidence_artifacts(run_dir, payload, stem="evidence")
        _append_standalone_log(state, job_id, log_path, f"Captured elements: {len(payload.get('elements', []))}")
        state.update_job(
            job_id,
            status="success",
            exit_code=0,
            evidence_url=f"/runs/{quote(run_dir.name)}/md/evidence.md",
            evidence_json_url=f"/runs/{quote(run_dir.name)}/json/evidence.json",
        )
    except Exception as exc:  # noqa: BLE001 - evidence failures should be visible in the workbench.
        _append_standalone_log(state, job_id, log_path, f"ERROR: {exc}")
        state.update_job(job_id, status="failed", exit_code=1, error=str(exc))


def _append_standalone_log(state, job_id: str, log_path: Path, line: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(line + "\n")
    state.append_log(job_id, line)


def _capture_evidence_payload(
    *,
    mode: str,
    target_url: str,
    selector_filter: str | None,
    cdp_port: int,
    browser_channel: str,
) -> dict:
    if mode == "url" and not target_url:
        raise ValueError("target_url is required in url mode")
    if target_url and not _is_allowed_browser_url(target_url):
        raise ValueError("target_url must start with http://, https://, file://, or chrome://")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = None
        context = None
        try:
            if mode == "url":
                launch_kwargs = {"headless": False}
                if browser_channel:
                    launch_kwargs["channel"] = browser_channel
                browser = playwright.chromium.launch(**launch_kwargs)
                context = browser.new_context(no_viewport=True)
                page = context.new_page()
                page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            else:
                browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{cdp_port}")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                if target_url:
                    page = context.new_page()
                    page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                else:
                    pages = context.pages
                    if not pages:
                        raise ValueError(f"No page is available from CDP port {cdp_port}")
                    page = pages[0]

            page.wait_for_timeout(500)
            elements = scan_page(page, selector_filter=selector_filter)
            return {
                "captured_at": datetime.now().isoformat(timespec="seconds"),
                "mode": mode,
                "target_url": target_url or page.url,
                "page_url": page.url,
                "page_title": _safe_page_title(page),
                "selector_filter": selector_filter,
                "cdp_port": cdp_port if mode != "url" else None,
                "elements": elements,
                "accessibility_snapshot": _collect_accessibility_snapshot(page),
            }
        finally:
            if context and mode == "url":
                context.close()
            if browser:
                browser.close()


def _is_allowed_browser_url(url: str) -> bool:
    lowered = url.strip().lower()
    return lowered.startswith(("http://", "https://", "file://", "chrome://"))


def _safe_page_title(page) -> str:
    try:
        return page.title()
    except Exception:
        return ""


def _collect_accessibility_snapshot(page) -> list[dict]:
    script = """
() => Array.from(document.querySelectorAll(
  "h1,h2,h3,h4,h5,h6,button,a[href],input,textarea,select,[role],[aria-label],[aria-labelledby]"
)).slice(0, 200).map((el, index) => ({
  index,
  tag: el.tagName.toLowerCase(),
  role: el.getAttribute("role") || "",
  aria_label: el.getAttribute("aria-label") || "",
  name: el.getAttribute("name") || "",
  id: el.id || "",
  type: el.getAttribute("type") || "",
  text: (el.innerText || el.getAttribute("value") || el.getAttribute("placeholder") || "").trim().slice(0, 120),
  visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
}))
"""
    try:
        return page.evaluate(script)
    except Exception:
        return []


def _write_evidence_artifacts(run_dir: Path, payload: dict, stem: str) -> None:
    md_dir = run_dir / "md"
    json_dir = run_dir / "json"
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / f"{stem}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (md_dir / f"{stem}.md").write_text(_build_evidence_markdown(payload), encoding="utf-8")


def _build_evidence_markdown(payload: dict) -> str:
    lines = [
        "# 页面证据采集",
        "",
        f"- Captured at: `{payload.get('captured_at', '')}`",
        f"- Mode: `{payload.get('mode', '')}`",
        f"- Target URL: `{payload.get('target_url', '')}`",
        f"- Page URL: `{payload.get('page_url', '')}`",
        f"- Page title: `{payload.get('page_title', '')}`",
        f"- Selector filter: `{payload.get('selector_filter') or ''}`",
        "",
        "## 元素证据",
        "",
        format_evidence_table(payload.get("elements", [])),
        "",
        "## 可访问性摘要",
        "",
        "| 序号 | 标签 | role | name/id | 文本 | 可见 |",
        "|---|---|---|---|---|---|",
    ]
    for item in payload.get("accessibility_snapshot", []):
        name = item.get("name") or item.get("id") or item.get("aria_label") or ""
        text = str(item.get("text") or "").replace("|", "\\|")
        lines.append(
            f"| {item.get('index')} | {item.get('tag')} | {item.get('role') or ''} | {name} | {text} | {item.get('visible')} |"
        )
    return "\n".join(lines).strip() + "\n"


def maybe_write_failure_evidence_diff(
    *,
    state,
    job_id: str,
    run_dir: Path | None,
    test_path: str,
    log_path: Path,
    target_url: str,
    mode: str,
    selector_filter: str | None,
    cdp_port: int,
    browser_channel: str,
) -> None:
    if not run_dir:
        return
    try:
        current = None
        if mode in EVIDENCE_MODES and (target_url or mode != "url"):
            current = _capture_evidence_payload(
                mode=mode,
                target_url=target_url,
                selector_filter=selector_filter,
                cdp_port=cdp_port,
                browser_channel=browser_channel,
            )
            _write_evidence_artifacts(run_dir, current, stem="evidence_current")
        baseline = _read_evidence_payload(run_dir / "json" / "evidence.json")
        diff = _build_evidence_diff_payload(
            baseline=baseline,
            current=current,
            test_path=test_path,
            log_path=log_path,
        )
        _write_evidence_diff_artifacts(run_dir, diff)
        state.append_log(job_id, f"Evidence diff: {run_dir / 'md' / 'evidence_diff.md'}")
    except Exception as exc:  # noqa: BLE001 - do not hide the test result behind diff generation.
        state.append_log(job_id, f"Evidence diff skipped: {exc}")


def _read_evidence_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _build_evidence_diff_payload(
    *,
    baseline: dict,
    current: dict | None,
    test_path: str,
    log_path: Path,
) -> dict:
    baseline_elements = baseline.get("elements", []) if baseline else []
    current_elements = current.get("elements", []) if current else []
    current_selectors = {_top_selector(el) for el in current_elements if _top_selector(el)}
    missing = []
    for element in baseline_elements:
        selector = _top_selector(element)
        if selector and current_elements and selector not in current_selectors:
            missing.append({
                "original_selector": selector,
                "element": _element_label(element),
                "dom_fragment": element.get("dom_fragment", ""),
                "candidate_replacements": _candidate_replacements(element, current_elements),
            })
    return {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "test_path": test_path,
        "log_path": str(log_path),
        "baseline_available": bool(baseline_elements),
        "current_available": bool(current_elements),
        "baseline_url": baseline.get("page_url") if baseline else "",
        "current_url": current.get("page_url") if current else "",
        "missing_or_changed_elements": missing,
        "log_tail": _read_log_tail(log_path),
        "recommendation": [
            "Do not auto-edit selectors from this report alone.",
            "Confirm the current DOM evidence, then make the smallest selector/page-object/test change.",
        ],
    }


def _top_selector(element: dict) -> str:
    selectors = element.get("candidate_selectors") or []
    if not selectors:
        return ""
    first = selectors[0]
    return str(first.get("selector") if isinstance(first, dict) else first)


def _element_label(element: dict) -> str:
    parts = [str(element.get("tag") or "").upper()]
    if element.get("id"):
        parts.append("#" + str(element.get("id")))
    if element.get("name"):
        parts.append("[name=" + str(element.get("name")) + "]")
    text = str(element.get("text") or "").strip()
    if text:
        parts.append(text[:60])
    return " ".join(part for part in parts if part)


def _candidate_replacements(baseline_element: dict, current_elements: list[dict]) -> list[dict]:
    baseline_text = str(baseline_element.get("text") or "").strip().lower()
    baseline_name = str(baseline_element.get("name") or "").strip().lower()
    baseline_tag = str(baseline_element.get("tag") or "").strip().lower()
    scored = []
    for element in current_elements:
        score = 0
        if baseline_tag and baseline_tag == str(element.get("tag") or "").lower():
            score += 2
        if baseline_name and baseline_name == str(element.get("name") or "").lower():
            score += 5
        text = str(element.get("text") or "").strip().lower()
        if baseline_text and (baseline_text in text or text in baseline_text):
            score += 3
        selector = _top_selector(element)
        if score and selector:
            scored.append({
                "selector": selector,
                "score": score,
                "element": _element_label(element),
                "dom_fragment": element.get("dom_fragment", ""),
            })
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:5]


def _read_log_tail(log_path: Path, max_lines: int = 80) -> list[str]:
    if not log_path.exists():
        return []
    try:
        return log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
    except OSError:
        return []


def _write_evidence_diff_artifacts(run_dir: Path, payload: dict) -> None:
    md_dir = run_dir / "md"
    json_dir = run_dir / "json"
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / "evidence_diff.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (md_dir / "evidence_diff.md").write_text(_build_evidence_diff_markdown(payload), encoding="utf-8")


def _build_evidence_diff_markdown(payload: dict) -> str:
    lines = [
        "# 失败后证据差异报告",
        "",
        f"- Created at: `{payload.get('created_at', '')}`",
        f"- Test path: `{payload.get('test_path', '')}`",
        f"- Baseline URL: `{payload.get('baseline_url') or ''}`",
        f"- Current URL: `{payload.get('current_url') or ''}`",
        "",
        "## 缺失或变化的元素",
        "",
        "| 原 selector | 元素 | 候选替代 |",
        "|---|---|---|",
    ]
    missing = payload.get("missing_or_changed_elements", [])
    if not missing:
        lines.append("| - | 未发现可对比的 selector 差异，或缺少 baseline/current evidence。 | - |")
    for item in missing:
        candidates = "<br>".join(
            f"`{candidate.get('selector')}` score={candidate.get('score')}"
            for candidate in item.get("candidate_replacements", [])
        ) or "-"
        lines.append(f"| `{item.get('original_selector')}` | {item.get('element')} | {candidates} |")
    lines.extend([
        "",
        "## 建议",
        "",
    ])
    for recommendation in payload.get("recommendation", []):
        lines.append(f"- {recommendation}")
    lines.extend(["", "## 测试日志尾部", "", "```text"])
    lines.extend(payload.get("log_tail", []))
    lines.append("```")
    return "\n".join(lines).strip() + "\n"


