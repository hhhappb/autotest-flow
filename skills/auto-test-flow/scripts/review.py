"""Review rules and human-facing review notes for auto-test-flow."""

from __future__ import annotations

import json


def build_review_result(
    *,
    test_cases: dict,
    automation_request: dict,
    execution_request: dict,
    project_context_discovery: dict,
    review_policy: str,
) -> dict:
    """Review generated artifacts with deterministic risk rules before Codex handoff."""
    findings = []
    cases = test_cases.get("cases", [])
    selected_cases = automation_request.get("selected_cases", [])
    context_needed = automation_request.get("project_context_needed", [])
    required_environment = execution_request.get("required_environment", [])
    pre_run_checks = execution_request.get("pre_run_checks", [])
    executable_steps = execution_request.get("executable_steps", [])

    if isinstance(selected_cases, str):
        selected_cases = [selected_cases]
    if isinstance(context_needed, str):
        context_needed = [context_needed]
    if isinstance(executable_steps, dict):
        executable_steps = [executable_steps]
    if not isinstance(executable_steps, list):
        executable_steps = []

    generated_text = json.dumps({
        "test_cases": test_cases,
        "automation_request": automation_request,
        "execution_request": execution_request,
    }, ensure_ascii=False)
    for pattern in project_context_discovery.get("forbidden_patterns", []):
        if pattern and pattern.lower() in generated_text.lower():
            findings.append(_finding(
                "high",
                "project_context_conflict",
                f"Generated artifact conflicts with project discovery forbidden pattern: {pattern}",
                "Regenerate or edit the artifact so it follows project_context_discovery.hard_constraints and existing project structure.",
            ))

    if project_context_discovery.get("status") == "discovered":
        command_values = []
        for command_field in ("commands", "command_candidates"):
            value = execution_request.get(command_field, [])
            if isinstance(value, str):
                command_values.append(value)
            elif isinstance(value, list):
                command_values.extend(str(item) for item in value)
        commands_text = "\n".join(command_values)
        if "pytest tests/" in commands_text.replace("\\", "/"):
            findings.append(_finding(
                "high",
                "execution_command_conflict",
                "Execution request uses a generic pytest tests/ command even though project discovery found a concrete project layout.",
                "Use project_context_discovery.recommended_commands or an explicitly discovered project test path.",
            ))

    if len(cases) > 12:
        findings.append(_finding(
            "medium",
            "case_scope",
            f"结构化测试用例数量为 {len(cases)}，可能超过首批自动化落地范围。",
            "建议人工删减或调整 P0/P1 自动化候选用例。",
        ))

    if len(selected_cases) > 8:
        findings.append(_finding(
            "medium",
            "automation_scope",
            f"首批自动化候选用例数量为 {len(selected_cases)}，可能导致一次落地范围过大。",
            "建议优先保留核心 P0 和少量关键 P1。",
        ))

    placeholder_cases = [
        case for case in cases
        if isinstance(case, dict) and _looks_placeholder_text(json.dumps(case, ensure_ascii=False))
    ]
    runnable_placeholder_cases = [
        case for case in placeholder_cases
        if str(case.get("priority", "")).upper() == "P0" or bool(case.get("automation_candidate"))
    ]
    if runnable_placeholder_cases:
        findings.append(_finding(
            "high",
            "placeholder_case",
            f"发现 {len(runnable_placeholder_cases)} 条占位或待确认用例被标记为 P0 或自动化候选。",
            "占位用例必须降级为待确认/非自动化候选，不能进入 Codex 代码落地。",
        ))
    elif placeholder_cases:
        findings.append(_finding(
            "medium",
            "placeholder_case",
            f"发现 {len(placeholder_cases)} 条占位或待确认用例，当前测试流程仍不完整。",
            "补齐真实界面、操作路径、预期结果和元素证据后，再生成最终测试流程。",
        ))

    if not executable_steps:
        findings.append(_finding(
            "high",
            "test_flow_steps",
            "执行请求缺少 executable_steps，审查人无法看到可审查的测试流程或发现/证据采集步骤。",
            "execution_request.json 必须包含 executable_steps；信息不足时也要输出 project_discovery 和 evidence_capture 步骤。",
        ))
    else:
        weak_steps = [
            step for step in executable_steps
            if isinstance(step, dict) and _step_is_weak_or_placeholder(step)
        ]
        if weak_steps:
            findings.append(_finding(
                "high",
                "test_flow_steps",
                f"发现 {len(weak_steps)} 个执行步骤缺少明确页面/操作/预期结果，或仍包含待确认占位文本。",
                "最终测试流程必须说明打开什么界面、做什么操作、观察什么结果；信息不足时应停留在发现/证据采集步骤。",
            ))

    if len(context_needed) > 8:
        findings.append(_finding(
            "medium",
            "project_context",
            "自动化实现请求需要大量项目上下文，说明当前上下文不够确定。",
            "Codex 写代码前应先做项目发现，并输出修改计划。",
        ))

    recommended_framework = str(automation_request.get("recommended_framework", ""))
    if recommended_framework and recommended_framework not in ("existing_project_framework", "待项目发现"):
        findings.append(_finding(
            "medium",
            "framework_choice",
            f"自动化请求推荐了框架：{recommended_framework}",
            "如果项目已有测试框架，应优先使用已有框架。",
        ))

    target_type = str(automation_request.get("target_type", "unknown"))
    if target_type == "unknown":
        findings.append(_finding(
            "high",
            "target_type",
            "自动化目标类型仍为 unknown。",
            "Codex handoff 前应确认这是 Web UI、API、单元测试还是集成测试。",
        ))
    elif target_type == "web_ui" and not automation_request.get("element_evidence_required"):
        findings.append(_finding(
            "medium",
            "element_evidence",
            "Web UI 自动化请求未显式声明 element_evidence_required。",
            "Codex 写选择器、页面对象或 UI 流程前必须先采集或请求 CDP/F12 元素证据。",
        ))
    elif target_type == "web_ui":
        ui_steps_requiring_evidence = [
            step for step in executable_steps
            if isinstance(step, dict)
            and str(step.get("action", "")) in {"click", "fill", "select", "assert"}
            and str(step.get("selector_status", "")) != "confirmed"
        ]
        if ui_steps_requiring_evidence:
            findings.append(_finding(
                "high",
                "element_evidence",
                f"发现 {len(ui_steps_requiring_evidence)} 个 Web UI 点击/输入/选择/断言步骤尚未确认 selector 或 DOM 证据。",
                "先采集 CDP/F12/真实 DOM 证据，再允许生成最终 UI 操作流程或修改 selector/page object。",
            ))

    readiness = project_context_discovery.get("test_flow_readiness", {})
    flow_status = str(readiness.get("flow_status", ""))
    if flow_status in {"blocked_pending_project_mapping", "needs_confirmation", "partial_from_existing_code"}:
        final_business_steps = [
            step for step in executable_steps
            if isinstance(step, dict)
            and str(step.get("phase", "")) not in {"project_discovery", "evidence_capture"}
            and bool(step.get("confirmed"))
        ]
        if final_business_steps:
            findings.append(_finding(
                "high",
                "test_flow_steps",
                "项目发现结果仍显示测试流程未完全确认，但执行请求包含已确认的最终业务步骤。",
                "在真实界面、已有代码映射、预期结果和元素证据确认前，不要把业务步骤标为 confirmed。",
            ))

    all_safety_text = "\n".join(
        str(item) for item in pre_run_checks + required_environment
    )
    safety_keywords = ["生产", "删除", "支付", "发消息", "改权限", "数据库连接", "测试数据库", "账号", "token", "Token"]
    matched_keywords = [word for word in safety_keywords if word in all_safety_text]
    if matched_keywords:
        findings.append(_finding(
            "high",
            "environment_safety",
            f"执行请求涉及环境/数据安全敏感项：{', '.join(sorted(set(matched_keywords)))}",
            "进入 Codex 代码落地或执行测试前，需要人工确认测试环境和测试数据安全。",
        ))

    assumptions = test_cases.get("assumptions", [])
    if assumptions:
        findings.append(_finding(
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
        "policy": review_policy,
        "decision": decision,
        "summary": summary,
        "counts": {
            "high": high_count,
            "medium": medium_count,
            "low": sum(1 for item in findings if item["severity"] == "low"),
        },
        "findings": findings,
        "gate": {
            "codex_handoff_allowed": decision != "blocked" or review_policy == "full-auto",
            "requires_user_confirmation": decision == "blocked" or review_policy == "ask",
        },
    }


def build_review_summary(review_result: dict) -> str:
    """Build the compact console review summary."""
    if not review_result:
        return "[REVIEW] 尚未生成审查结果。"
    counts = review_result.get("counts", {})
    return (
        f"[REVIEW] decision={review_result.get('decision')} | "
        f"high={counts.get('high', 0)}, medium={counts.get('medium', 0)}, "
        f"low={counts.get('low', 0)}\n"
        f"[REVIEW] {review_result.get('summary')}"
    )


def prompt_for_review_confirmation(review_result: dict) -> None:
    """Ask for manual confirmation and update the review gate in-place."""
    answer = input("\n审查完成。是否允许继续生成 Codex handoff？输入 yes/继续 确认：").strip().lower()
    if answer in ("yes", "y", "继续", "确认", "confirm"):
        review_result["user_confirmation"] = "confirmed"
        review_result["gate"]["codex_handoff_allowed"] = True
        return
    review_result["user_confirmation"] = "rejected"
    review_result["decision"] = "blocked"
    review_result["gate"]["codex_handoff_allowed"] = False


def should_build_codex_handoff(review_result: dict, review_policy: str) -> bool:
    """Return whether the Codex handoff artifacts should be generated."""
    if review_policy == "full-auto":
        return True
    gate = review_result.get("gate", {})
    return bool(gate.get("codex_handoff_allowed"))


def build_review_notes_markdown(review_result: dict, test_cases: dict) -> str:
    """Build a human-readable review report."""
    if not review_result:
        return "# 交接审查\n\n尚未生成审查结果。\n"

    decision = review_result.get("decision", "")
    findings = review_result.get("findings", [])
    assumptions = test_cases.get("assumptions", [])

    if decision == "pass":
        verdict = "当前可以进入 Codex 交接。"
        reason = "未发现阻塞项。你可以继续查看测试用例，或调用 Codex 接手落地。"
        operation = "可以调用 Codex。若本次涉及代码修改，仍建议让 Codex 先输出修改计划。"
    elif decision == "needs_attention":
        verdict = "当前可以继续，但建议先看完发现项。"
        reason = "本次没有阻塞项，但存在需要人工判断的风险。"
        operation = "看完下面的发现项后，如果你接受这些风险，可以调用 Codex。"
    else:
        verdict = "当前不建议直接调用 Codex。"
        reason = "发现需要人工确认的高风险项。"
        operation = "先确认下面的事项；确认无误后，可以调用 Codex。建议先让 Codex 读取项目并输出修改计划。"

    confirmation_items = _build_confirmation_items(findings, decision)
    lines = [
        "# 交接审查",
        "",
        "## 当前结论",
        "",
        verdict,
        "",
        reason,
        "",
        "## 继续前请确认",
        "",
    ]
    if confirmation_items:
        lines.extend(_markdown_list(confirmation_items))
    else:
        lines.append("- 暂无额外确认项。")
    lines.extend(["", "## 发现项", ""])

    if not findings:
        lines.append("- 暂无风险发现。")
    else:
        severity_names = {"high": "高风险", "medium": "中风险", "low": "低风险"}
        category_names = {
            "automation_scope": "自动化范围偏大",
            "environment_safety": "账号/环境安全",
            "assumptions": "测试假设待确认",
            "placeholder_case": "占位用例不可落地",
            "test_flow_steps": "测试流程不完整",
            "target_type": "目标类型不明确",
            "element_evidence": "页面元素证据不足",
            "framework_choice": "测试框架选择需确认",
            "project_context": "项目上下文不足",
            "execution_command_conflict": "执行命令不符合项目结构",
            "project_context_conflict": "项目结构约束冲突",
        }
        for item in findings:
            severity = item.get("severity", "")
            category = item.get("category", "")
            title = category_names.get(category, category or "风险项")
            lines.extend([
                f"### {severity_names.get(severity, '风险')}：{title}",
                "",
                item.get("message", ""),
                "",
                f"建议：{item.get('recommendation', '')}",
                "",
            ])

    if assumptions:
        lines.extend(["## 测试假设", ""])
        lines.extend(_markdown_list(assumptions))
        lines.append("")

    lines.extend(["## 建议操作", "", operation, ""])
    return "\n".join(lines)


def _finding(severity: str, category: str, message: str, recommendation: str) -> dict:
    return {
        "severity": severity,
        "category": category,
        "message": message,
        "recommendation": recommendation,
    }


def _build_confirmation_items(findings: list[dict], decision: str) -> list[str]:
    confirmation_items = []
    for item in findings:
        category = item.get("category", "")
        if category == "environment_safety":
            confirmation_items.extend([
                "使用的是测试账号，不是生产账号。",
                "当前环境是测试环境，不会影响真实店铺、生产数据或线上权限。",
                "如需要运行测试，先确认测试数据可以被读取或修改。",
            ])
        elif category == "automation_scope":
            confirmation_items.append("确认本次首批落地范围，必要时先保留核心 P0/P1 用例。")
        elif category == "target_type":
            confirmation_items.append("确认本次目标类型：Web UI、API、单元测试还是集成测试。")
        elif category == "element_evidence":
            confirmation_items.append("如果涉及页面点击、读取或断言，先准备真实 DOM/F12 元素证据。")
        elif category == "framework_choice":
            confirmation_items.append("确认使用项目已有测试框架，而不是引入新的测试框架。")
        elif category == "project_context":
            confirmation_items.append("让 Codex 先读取项目上下文，再输出修改计划。")
        elif category == "execution_command_conflict":
            confirmation_items.append("确认执行命令来自当前项目结构，不使用泛化命令。")
        elif category == "project_context_conflict":
            confirmation_items.append("确认生成内容符合当前项目结构和已有约束。")
        elif category == "assumptions":
            confirmation_items.append("让 Codex 先核对测试假设是否成立，再决定修改方案。")
        elif category == "placeholder_case":
            confirmation_items.append("补齐真实测试界面、操作步骤、预期结果后，再把用例标为 P0 或自动化候选。")
        elif category == "test_flow_steps":
            confirmation_items.append("确认测试流程已经说明打开什么界面、执行什么操作、观察和断言什么结果。")

    if decision == "blocked":
        confirmation_items.append("涉及代码修改前，Codex 仍需再次列出修改文件、修改点和验证命令，并等待确认。")

    seen = set()
    return [
        item for item in confirmation_items
        if item and not (item in seen or seen.add(item))
    ]


def _markdown_list(items) -> list[str]:
    if not items:
        return ["- 暂无"]
    if isinstance(items, str):
        return [f"- {items}"]
    return [f"- {item}" for item in items]


def _looks_placeholder_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(item in lowered for item in [
        "待补充",
        "待确认",
        "占位",
        "对应页面",
        "根据附件",
        "验证功能正常",
        "具体内容后补充",
        "to be confirmed",
        "tbd",
    ])


def _step_is_weak_or_placeholder(step: dict) -> bool:
    text = json.dumps(step, ensure_ascii=False)
    if _looks_placeholder_text(text):
        return True
    required = ["description", "page_or_location", "expected_result"]
    for key in required:
        value = str(step.get(key, "")).strip()
        if not value:
            return True
    return False
