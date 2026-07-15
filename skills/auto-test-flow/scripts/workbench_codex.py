#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex handoff execution helpers for the auto-test-flow workbench."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from urllib.parse import quote


AUTHORIZED_CODE_TARGETS = (
    "auto-test/project/feikua/selectors/finger_print_selectors.py",
    "auto-test/project/feikua/pages/login_page/finger_print_page/finger_print_page.py",
    "auto-test/project/feikua/testcases/test_finger_print.py",
)


def start_codex_execution_job(
    *,
    state,
    run_dir: Path,
    project_root: Path,
    approval_policy: str,
    extra_instruction: str,
    continue_mode: bool,
    execution_mode: str,
    load_input_materials_func,
    run_command_func,
    repair_mojibake_func,
    prepare_authorized_cdp_environment_func,
) -> dict:
    job = state.create_job("codex")
    job_id = job["id"]
    thread = threading.Thread(
        target=_run_codex_job,
        args=(
            state,
            job_id,
            run_dir,
            project_root,
            approval_policy,
            extra_instruction,
            continue_mode,
            execution_mode,
            load_input_materials_func,
            run_command_func,
            repair_mojibake_func,
            prepare_authorized_cdp_environment_func,
        ),
        daemon=True,
    )
    thread.start()
    return job


def _run_codex_job(
    state,
    job_id: str,
    run_dir: Path,
    project_root: Path,
    approval_policy: str,
    extra_instruction: str,
    continue_mode: bool,
    execution_mode: str,
    load_input_materials_func,
    run_command_func,
    repair_mojibake_func,
    prepare_authorized_cdp_environment_func,
) -> None:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"codex_exec_{job_id}.log"
    last_message_path = logs_dir / f"codex_last_message_{job_id}.md"
    sandbox = "workspace-write" if execution_mode == "authorized" else "read-only"
    codex_task_path = run_dir / "md" / "codex_task.md"
    if not codex_task_path.exists():
        state.update_job(job_id, status="failed", error=f"Missing {codex_task_path}")
        return
    materials = load_input_materials_func(run_dir)
    state.update_job(
        job_id,
        run_dir=str(run_dir),
        run_url=f"/runs/{quote(run_dir.name)}/index.html",
        last_message_file=str(last_message_path),
    )
    preflight_context = ""
    if execution_mode == "authorized":
        try:
            preflight_context = prepare_authorized_cdp_environment_func(state, job_id, project_root, logs_dir)
        except Exception as exc:  # noqa: BLE001 - report exact local preflight failure.
            state.append_log(job_id, f"授权执行预检失败: {exc}")
            state.update_job(job_id, status="failed", error=str(exc))
            return

    previous_decision = _find_latest_decision(run_dir) if continue_mode else None
    prompt = build_codex_prompt(
        run_dir=run_dir,
        project_root=project_root,
        codex_task_path=codex_task_path,
        materials=materials,
        extra_instruction=extra_instruction,
        previous_decision=previous_decision,
        execution_mode=execution_mode,
        preflight_context=preflight_context,
    )
    prompt_path = logs_dir / f"codex_prompt_{job_id}.md"
    prompt_path.write_text(prompt, encoding="utf-8")

    command = [
        state.codex_command,
        "exec",
        "-C",
        str(project_root),
        "--skip-git-repo-check",
        "--sandbox",
        sandbox,
        "-c",
        f"approval_policy={json.dumps(approval_policy)}",
        "--output-last-message",
        str(last_message_path),
    ]
    for material in materials:
        if material.get("kind") == "image":
            image_path = Path(material.get("path", ""))
            if image_path.exists():
                command.extend(["--image", str(image_path)])
    command.append("-")

    try:
        state.append_log(job_id, "Codex 已启动，执行日志会显示实时输出。")
        state.append_log(job_id, f"工作目录: {project_root}")
        state.append_log(job_id, f"执行模式: {execution_mode}，sandbox {sandbox}，审批策略 {approval_policy}")
        state.append_log(job_id, f"完整原始日志: {log_path}")
        exit_code = run_command_func(
            state,
            job_id,
            command,
            project_root,
            log_path,
            input_text=prompt,
            stream_output_to_job=True,
        )
        status = "success" if exit_code == 0 else "failed"
        summary_path = _write_codex_decision_summary(run_dir, job_id, last_message_path, exit_code, repair_mojibake_func)
        state.append_log(job_id, "")
        state.append_log(job_id, "Codex 决策摘要")
        for line in summary_path.read_text(encoding="utf-8", errors="replace").splitlines():
            state.append_log(job_id, line)
        state.append_log(job_id, "")
        state.append_log(job_id, f"Exit code: {exit_code}")
        state.update_job(job_id, status=status, exit_code=exit_code, summary_file=str(summary_path))
    except Exception as exc:  # noqa: BLE001 - show exact local failure.
        state.append_log(job_id, f"ERROR: {exc}")
        state.update_job(job_id, status="failed", error=str(exc))


def _write_codex_decision_summary(run_dir: Path, job_id: str, last_message_path: Path, exit_code: int, repair_mojibake_func) -> Path:
    logs_dir = run_dir / "logs"
    summary_path = logs_dir / f"codex_decision_{job_id}.md"
    if last_message_path.exists():
        text = repair_mojibake_func(last_message_path.read_text(encoding="utf-8", errors="replace")).strip()
    else:
        text = ""
    if not text:
        text = "Codex 没有写出最终消息。请查看完整原始日志定位原因。"
    lines = [
        f"退出码: {exit_code}",
        f"最终消息: {last_message_path}",
        "",
        _compact_final_message(text),
    ]
    summary_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return summary_path


def _compact_final_message(text: str, max_lines: int = 120) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    compact = []
    skip_markers = (
        "succeeded in ",
        "tokens used",
        "session id:",
        "OpenAI Codex",
        "--------",
    )
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(marker) for marker in skip_markers):
            continue
        compact.append(line)
        if len(compact) >= max_lines:
            compact.append("... 决策摘要已截断；完整内容见 codex_last_message 文件。")
            break
    return "\n".join(compact).strip()


def _find_latest_decision(run_dir: Path) -> str | None:
    logs_dir = run_dir / "logs"
    if not logs_dir.exists():
        return None
    decision_files = sorted(logs_dir.glob("codex_decision_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not decision_files:
        return None
    try:
        text = decision_files[0].read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    text = text.strip()
    return text[:6000] if len(text) > 6000 else text


STABLE_CODEX_PROMPT_RULES = """不可省略硬约束：
- 读取并遵守项目 AGENTS.md；如当前目录未看到，先检查父级目录。
- 优先读取结构化产物：json/test_cases.json、json/automation_request.json、json/execution_request.json、json/input_materials.json、md/test_cases.md、md/test_plan.md、md/codex_task.md。
- 不默认重新解析原始 .xlsx/.xls；只有结构化产物缺失、冲突，或用户明确要求核对时才说明原因并读取。
- Windows 下不要通过 PowerShell inline command 或 python -c 传递中文店铺名、账号名、页面标题等测试数据；含中文数据优先来自 UTF-8 源码、JSON 或产物文件。
- UI selector、page object、点击、读取、断言、流程修改前必须有 CDP/F12/真实 DOM 证据；缺证据时先输出证据采集计划，不猜 selector，不加兜底。
- 修改保持最小可验证范围；不要削弱断言、隐藏产品问题，或为了通过测试吞掉真实失败。
- 授权执行模式仅允许交接范围和白名单内最小修改；新增/删除文件、安装依赖、扩大范围、保存或修改店铺/测试环境数据、管理员权限命令，都需要新的确认。
- 只读分析模式不得修改代码或运行会改变数据的命令；如需授权，列出范围、命令和停止条件。
- 最终消息必须包含：结论、是否需要用户确认、准备修改/已修改文件、修改原因、已执行/未执行命令、剩余风险。"""


def build_codex_prompt(
    run_dir: Path,
    project_root: Path,
    codex_task_path: Path,
    materials: list[dict],
    extra_instruction: str,
    previous_decision: str | None = None,
    execution_mode: str = "analysis",
    preflight_context: str = "",
) -> str:
    codex_task = _compact_codex_task_for_prompt(codex_task_path.read_text(encoding="utf-8", errors="replace"))
    if execution_mode == "authorized":
        targets = "、".join(f"`{target}`" for target in AUTHORIZED_CODE_TARGETS)
        edit_mode = (
            "授权执行模式：用户已对本轮交接任务给出一次性授权。"
            f"可修改白名单文件（{targets}）、读取 CDP/DOM、运行验证命令、最小修复和复测。"
            "超出白名单或交接范围、修改店铺/测试环境数据、新增/删除文件、安装依赖、管理员权限命令，必须重新确认。"
        )
    else:
        edit_mode = (
            "只读分析模式：只能读取项目、产物和日志，输出接管分析、证据采集计划和最小修改方案。"
            "不要修改代码文件，不要运行会改变测试环境数据的命令。"
            "如需进入修改或验证，在最终消息中列出授权执行所需范围、命令和停止条件。"
        )
    extra = f"\n\n用户补充指令：\n{extra_instruction}\n" if extra_instruction else ""
    continue_context = ""
    if previous_decision:
        continue_context = f"""
## 上轮执行决策（继续模式）

以下是上一次 Codex 执行的决策摘要。用于延续上下文；若与本轮用户指令或项目规则冲突，以本轮用户指令和项目规则为准。

```markdown
{previous_decision}
```

"""
    preflight_section = ""
    if preflight_context:
        preflight_section = f"""
## 本轮执行前预检

```markdown
{preflight_context}
```

"""
    materials_text = _build_codex_materials_text(materials)
    return f"""你现在由 auto-test-flow 本地工作台调用。

## 稳定规则

{STABLE_CODEX_PROMPT_RULES}

## 执行模式

{edit_mode}

## 动态任务上下文

项目根目录：
{project_root}

交接产物目录：
{run_dir}

输入材料：
{materials_text}
{continue_context}
{preflight_section}
{extra}

## 精简交接摘要

下面是由 `md/codex_task.md` 压缩生成的摘要；如摘要不足，再按路径读取完整产物：

```markdown
{codex_task}
```
"""


def _compact_codex_task_for_prompt(text: str) -> str:
    target = _extract_markdown_section(text, "任务目标")
    required = _extract_markdown_section(text, "必读产物")
    summary = _extract_markdown_section(text, "当前测试设计摘要")
    summary = _strip_fenced_blocks(summary)

    lines = [
        "# Codex Handoff Task（精简版）",
        "",
        "完整交接文件仍保存在 `md/codex_task.md`；当前 prompt 只保留执行所需的最小摘要，避免重复规则和大段 JSON 增加处理时间。",
        "",
        "## 任务目标",
        "",
        _compact_text(target, 1200) or "基于 Phase 2 产物接管自动化测试代码落地，读取结构化产物后输出修改计划、等待确认，并在允许后进行最小必要实现和验证。",
        "",
        "## 必读结构化产物",
        "",
        "- `md/codex_task.md`：完整交接说明，只有摘要不足时再读取。",
        "- `md/test_plan.md`：测试方案可读版。",
        "- `md/test_cases.md`：测试用例可读版。",
        "- `json/test_cases.json`：结构化测试用例。",
        "- `json/automation_request.json`：自动化实现请求、目标文件和风险。",
        "- `json/execution_request.json`：运行命令、环境要求和失败分类。",
        "- `json/input_materials.json`：原始材料摘要和附件路径。",
        "- `raw/raw_requirement.txt`：原始需求文本。",
        "",
    ]
    if required:
        lines.extend([
            "## 原交接文件列出的必读产物",
            "",
            _compact_text(required, 900),
            "",
        ])
    if summary:
        lines.extend([
            "## 当前测试设计摘要（已移除内嵌 JSON，按需读取 json/ 文件）",
            "",
            _compact_text(summary, 1600),
            "",
        ])
    return "\n".join(line.rstrip() for line in lines).strip()


def _extract_markdown_section(text: str, title: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(title)}\s*$", re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    next_heading = re.search(r"^##\s+", text[match.end():], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(text)
    return text[match.end():end].strip()


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```[\s\S]*?```", "（内嵌代码块/JSON 已省略；请读取对应 json/ 文件。）", text).strip()


def _compact_text(text: str, max_chars: int) -> str:
    compact = re.sub(r"\n{3,}", "\n\n", text.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[:max_chars].rstrip() + "\n...（已截断；完整内容见 `md/codex_task.md` 或对应结构化产物。）"


def _build_codex_materials_text(materials: list[dict]) -> str:
    if not materials:
        return "无附件材料。"
    lines = []
    for material in materials:
        lines.append(
            f"- {material.get('name')} ({material.get('kind')}, {material.get('size')} bytes): {material.get('path')}"
        )
    lines.append("")
    lines.append("默认不要重新解析原始附件；优先读取 json/input_materials.json 和其他结构化产物。只有结构化产物缺失、冲突或用户明确要求时，才请求读取原始附件。")
    return "\n".join(lines)


