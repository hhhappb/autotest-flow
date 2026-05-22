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
from project_discovery import (
    build_project_context_discovery_markdown,
    discover_project_context,
)
from review import (
    build_review_notes_markdown,
    build_review_result,
    build_review_summary,
    prompt_for_review_confirmation,
    should_build_codex_handoff,
)
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
    EXTRACT_FIELDS_SYSTEM_PROMPT,
    INTAKE_VALIDATION_SYSTEM_PROMPT,
    TEST_CASES_JSON_SYSTEM_PROMPT,
    TEST_PLAN_SYSTEM_PROMPT,
)


REVIEW_POLICIES = ("ask", "auto-review", "full-auto")


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

    def _step_call_api(self, label: str, system_prompt: str, user_message: str,
                       temperature: float, max_tokens: int, result_key: str) -> dict:
        """Print header, call API, parse JSON, return dict -- used by steps 1/4/5/6."""
        print("=" * 60)
        print(f"[{label}] ...")
        print("=" * 60)
        raw = self._call_api(system_prompt, user_message, temperature, max_tokens)
        return self._ensure_dict(self._parse_json(raw), result_key)

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
        self.fields = self._step_call_api(
            "Step 1/9 提取结构化字段",
            EXTRACT_FIELDS_SYSTEM_PROMPT,
            f"请从以下测试需求中提取字段：\n\n{self.requirement_text}",
            0.2, 2048, "fields")
        print(json.dumps(self.fields, ensure_ascii=False, indent=2))
        return self.fields

    def step_discover_project_context(self, raw_requirement: str) -> dict:
        """Discover existing project structure before generating plan and cases."""
        print("=" * 60)
        print("[Step 2/9] Discovering existing project context...")
        print("=" * 60)

        self.project_context_discovery = discover_project_context(
            raw_requirement=raw_requirement,
            requirement_text=self.requirement_text,
            fields=self.fields,
            cwd=Path.cwd(),
        )
        if self.project_context_discovery.get("status") == "not_found":
            print("No project root discovered; downstream prompts will require repository inspection.")
            return self.project_context_discovery
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
        self.test_cases = self._step_call_api(
            "Step 4/9 生成结构化测试用例",
            TEST_CASES_JSON_SYSTEM_PROMPT,
            self._build_test_cases_user_prompt(),
            PLAN_TEMPERATURE, PLAN_MAX_TOKENS, "test_cases")
        print(f"生成用例数量: {len(self.test_cases.get('cases', []))}")
        return self.test_cases

    # ═══ Step 5: Build Automation Request ═══
    def step_build_automation_request(self) -> dict:
        self.automation_request = self._step_call_api(
            "Step 5/9 生成自动化脚本实现请求",
            AUTOMATION_REQUEST_JSON_SYSTEM_PROMPT,
            self._build_automation_request_prompt(),
            0.2, 4096, "automation_request")
        print(json.dumps(self.automation_request, ensure_ascii=False, indent=2)[:800])
        return self.automation_request

    # ═══ Step 6: Build Execution Request ═══
    def step_build_execution_request(self) -> dict:
        self.execution_request = self._step_call_api(
            "Step 6/9 生成执行请求",
            EXECUTION_REQUEST_JSON_SYSTEM_PROMPT,
            self._build_execution_request_prompt(),
            0.2, 4096, "execution_request")
        print(json.dumps(self.execution_request, ensure_ascii=False, indent=2)[:800])
        return self.execution_request

    # ═══ Step 7: Review Gate ═══
    def step_review_gate(self) -> dict:
        """Step 7: Review generated artifacts before Codex handoff."""
        print("=" * 60)
        print(f"[Step 7/9] 审查生成产物 (policy: {self.review_policy})...")
        print("=" * 60)

        self.review_result = build_review_result(
            test_cases=self.test_cases,
            automation_request=self.automation_request,
            execution_request=self.execution_request,
            project_context_discovery=self.project_context_discovery,
            review_policy=self.review_policy,
        )
        print(build_review_summary(self.review_result))

        if self.review_policy == "ask":
            prompt_for_review_confirmation(self.review_result)
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
        self._write_text(md_dir / "review_notes.md", build_review_notes_markdown(self.review_result, self.test_cases))

        if full_artifacts:
            self._write_json(full_dir / "fields.json", self.fields)
            self._write_json(full_dir / "project_context_discovery.json", self.project_context_discovery)
            self._write_text(full_dir / "project_context_discovery.md", build_project_context_discovery_markdown(self.project_context_discovery))
            self._write_json(full_dir / "review_result.json", self.review_result)
            self._write_text(full_dir / "review_notes.md", build_review_notes_markdown(self.review_result, self.test_cases))
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

    @staticmethod
    def _extract_feature_name(text: str, max_len: int = 20) -> str:
        """Extract a short feature name from the raw requirement."""
        name = text.strip().split("\n")[0].strip()
        for prefix in ["测试", "请", "帮我", "我要", "需要"]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        name = re.sub(r"[^\u4e00-\u9fff\w]", "_", name)
        return name[:max_len].strip("_") or "test_plan"

    def _project_context_prompt(self) -> str:
        if not self.project_context_discovery:
            return "Project context discovery has not run. Do not invent project files or commands."
        return f"""Project context discovery has already inspected the local repository before this artifact was generated.
You must obey the discovered project_root, framework_signals, relevant_files, recommended_commands, and hard_constraints.
If a requested file/class/fixture/command conflicts with discovery, prefer discovery and add a need-confirmation item.
Do not invent files, classes, selectors, fixtures, or execution commands that conflict with this context.

{self._json_block(self.project_context_discovery)}"""

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
3. 如果输入材料已经明确给出测试点或测试用例，这些内容就是唯一测试范围；只做结构化整理，不额外扩写异常、边界、权限、安全或兼容性场景。
4. 如果材料中一行只表达一个测试点，默认只对应一个用例；用户只指定其中一个功能点时，不要展开同一材料中的其他功能点。
5. 如果需求与已有代码逻辑存在不确定点，请写入待确认问题，不要把假设写成确定预期。"""

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
3. 如果输入材料已经明确给出测试点或测试用例，把它们视为唯一事实来源，只生成这些已明确范围内的用例；不要扩展未指定功能点。
4. 如果材料中一行只表达一个测试点，默认只生成一个对应用例；除非该行本身明确包含多个场景。
5. 不要主动新增异常、边界、权限、安全、兼容性、容错或回归场景；只有材料或用户明确要求时才生成。
6. 如果信息缺失，写入 need_confirmation，不要通过新增用例来补全。
7. 不要输出要求新增未知测试文件、未知页面对象、未知 fixture 的用例前置条件。"""

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
4. 如果 automation_request.element_evidence_required 为 true，将"确认已完成 CDP/F12 元素证据采集"写入 pre_run_checks。"""

    def _set_skipped_codex_handoff(self) -> None:
        print("=" * 60)
        print("[Step 8/9] Codex handoff 已跳过")
        print("=" * 60)
        print(f"跳过原因: {self.review_result.get('summary', 'Review gate blocked handoff.')}")
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
                    "调用或遵循 karpathy-12-rules",
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
            "karpathy_12_rules_gate": {
                "required_for_codex_code_work": True,
                "skill_name": "karpathy-12-rules",
                "fallback_if_skill_unavailable": [
                    "先读项目代码和既有测试结构，再提出修改计划。",
                    "只做最小必要修改，不扩展未请求功能。",
                    "不新增 speculative abstraction、兜底选择器、宽泛重试或吞异常逻辑。",
                    "暴露假设、冲突和缺失证据，不静默猜测。",
                    "用最窄的相关命令验证结果，并报告未验证项。",
                ],
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

        owner = self.codex_task.get("owner_split", {})
        deepseek_items = "\n".join(f"- {item}" for item in owner.get("deepseek", []))
        codex_items = "\n".join(f"- {item}" for item in owner.get("codex_gpt_55", []))

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
- `json/input_materials.json`
- `raw/raw_requirement.txt`

## DeepSeek 与 Codex 职责边界

- DeepSeek 负责：
{deepseek_items}
- Codex/GPT-5.5 负责：
{codex_items}

## 当前测试设计摘要

- 结构化字段：读取 `json/input_materials.json` 和 `md/requirement.md`。
- 测试方案：读取 `md/test_plan.md`。
- 测试用例：读取 `json/test_cases.json` 和 `md/test_cases.md`。
- 自动化实现请求：读取 `json/automation_request.json`。
- 执行请求：读取 `json/execution_request.json`。
- 审查结论：读取 `md/review_notes.md` 和 `json/review_result.json`。
- 原始需求：只在结构化产物缺失或互相冲突时，再读取 `raw/raw_requirement.txt`。

## 下一步建议

1. 优先读取上述 `json/` 与 `md/` 结构化产物，不要默认重新解析原始 `.xlsx/.xls` 附件。
2. 在目标项目中执行只读发现，复核测试框架、目录结构、页面对象、选择器、fixtures 和运行命令。
3. 基于 `json/test_cases.json` 选择首批 P0/P1 自动化候选用例。
4. 如果运行时存在 `full/` 目录，可读取其中的 project context 和 review 补充产物。
5. 如涉及 Web UI，先采集或请求 CDP/F12 元素证据，并输出元素证据表。
6. 输出代码修改计划并等待用户确认。
"""

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
        visible_requirement = self._format_visible_requirement(raw_requirement)
        return f"""# 自动化测试工作流报告

> 生成时间: {timestamp}
> 模型: {MODEL}
> 阶段: Phase 2.6 pipeline with review policy and Codex handoff

## 原始需求

{visible_requirement}

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

    @staticmethod
    def _format_visible_requirement(raw_requirement: str) -> str:
        """Keep the human report readable by hiding long attachment extracts."""
        lines = []
        skipping_summary = False
        for line in raw_requirement.splitlines():
            stripped = line.strip()
            if stripped == "摘要：":
                skipping_summary = True
                continue
            if skipping_summary and line.startswith("- "):
                skipping_summary = False
            if skipping_summary:
                continue
            lines.append(line)
        return "\n".join(lines).strip() or "未提供可展示的需求文本。"

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
            if should_build_codex_handoff(self.review_result, self.review_policy):
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
