#!/usr/bin/env python3
"""Phase 2.6: 自动化测试工作流编排器

流程:
    粗略需求
    → Boost 优化
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
    python orchestrator.py --file req.txt --skip-boost
"""

import argparse
import html
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from anthropic import Anthropic

from config import (
    ANTHROPIC_AUTH_TOKEN,
    ANTHROPIC_BASE_URL,
    MODEL,
    SYSTEM_PROMPT_BOOST,
    BOOST_TEMPERATURE,
    BOOST_MAX_TOKENS,
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


EXTRACT_FIELDS_SYSTEM_PROMPT = """你是一个需求分析师。请从以下优化后的测试需求描述中，提取出关键字段。
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


class TestWorkflowOrchestrator:
    """Phase 2.6 orchestrator with review policy and Codex handoff."""

    def __init__(self):
        self.client = Anthropic(
            auth_token=ANTHROPIC_AUTH_TOKEN,
            base_url=ANTHROPIC_BASE_URL,
        )
        self.boosted_text = ""
        self.fields = {}
        self.test_plan = ""
        self.test_cases = {}
        self.automation_request = {}
        self.execution_request = {}
        self.project_context_request = {}
        self.codex_task = {}
        self.review_policy = "auto-review"
        self.review_result = {}

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

    # ═══ Step 1: Boost Prompt ═══
    def step_boost(self, raw_requirement: str) -> str:
        """Step 1: Boost/optimize the rough test requirement."""
        print("=" * 60)
        print("[Step 1/9] 优化测试需求提示词...")
        print("=" * 60)

        self.boosted_text = self._call_api(
            system_prompt=SYSTEM_PROMPT_BOOST,
            user_message=f"请帮我优化以下测试需求描述：\n\n{raw_requirement}",
            temperature=BOOST_TEMPERATURE,
            max_tokens=BOOST_MAX_TOKENS,
        )
        print(f"\n{self.boosted_text}\n")
        return self.boosted_text

    def step_review_boosted_requirement(self, raw_requirement: str,
                                        output_dir: str = None) -> str:
        """Review and optionally edit the boosted requirement before continuing."""
        print("=" * 60)
        print("[Step 1.5/9] 审查增强后的测试需求...")
        print("=" * 60)

        review_dir = self._create_boost_review_dir(raw_requirement, output_dir)
        boosted_path = review_dir / "boosted_requirement.md"
        html_path = review_dir / "index.html"

        self._write_text(review_dir / "raw_requirement.txt", raw_requirement)
        self._write_text(boosted_path, self.boosted_text)
        self._write_text(html_path, self._build_html_viewer(review_dir))

        print(f"\n增强需求审查目录: {review_dir}")
        print(f"  - boosted_requirement.md: {boosted_path}")
        print(f"  - index.html: {html_path}")
        print("\n请先审查增强后的需求。")
        print("输入 yes/继续: 使用当前 boosted_requirement.md 继续")
        print("输入 edit/编辑: 先编辑 boosted_requirement.md，保存后再回到这里输入 yes/继续")
        print("输入 no/取消: 停止 pipeline")

        while True:
            try:
                answer = input("\n是否继续使用增强后的需求？").strip().lower()
            except EOFError as exc:
                raise RuntimeError("Boosted requirement review requires interactive confirmation.") from exc

            if answer in {"yes", "y", "继续", "确认"}:
                self.boosted_text = boosted_path.read_text(encoding="utf-8")
                self._write_text(html_path, self._build_html_viewer(review_dir))
                print("\n已确认增强需求，继续后续 pipeline。")
                return self.boosted_text
            if answer in {"edit", "编辑", "e"}:
                print(f"\n请编辑并保存: {boosted_path}")
                print(f"也可以打开浏览器查看: {html_path}")
                print("编辑完成后回到这里输入 yes/继续。")
                continue
            if answer in {"no", "n", "取消", "stop", "停止"}:
                raise RuntimeError("Pipeline stopped during boosted requirement review.")
            print("请输入 yes/继续、edit/编辑 或 no/取消。")

    # ═══ Step 2: Extract Fields ═══
    def step_extract_fields(self) -> dict:
        """Step 2: Extract structured fields from boosted requirement."""
        print("=" * 60)
        print("[Step 2/9] 提取结构化字段...")
        print("=" * 60)

        fields_json = self._call_api(
            system_prompt=EXTRACT_FIELDS_SYSTEM_PROMPT,
            user_message=f"请从以下测试需求中提取字段：\n\n{self.boosted_text}",
            temperature=0.2,
            max_tokens=2048,
        )
        self.fields = self._ensure_dict(self._parse_json(fields_json), "fields")
        print(json.dumps(self.fields, ensure_ascii=False, indent=2))
        return self.fields

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
        print("[Step 8/9] 生成 Codex handoff 任务包...")
        print("=" * 60)

        self.project_context_request = self._build_project_context_request()
        self.codex_task = self._build_codex_task_json()
        print(json.dumps(self.codex_task, ensure_ascii=False, indent=2)[:800])
        return self.codex_task

    # ═══ Step 9: Save Output ═══
    def step_save_output(self, raw_requirement: str,
                         output_dir: str = None) -> str:
        """Step 9: Save all artifacts to a dedicated folder.

        Creates: output/<feature>_<timestamp>/
                   raw_requirement.txt
                   boosted_requirement.md
                   fields.json
                   test_plan.md
                   test_cases.md
                   test_cases.json
                   automation_request.json
                   execution_request.json
                   review_result.json
                   review_notes.md
                   project_context_request.json
                   codex_task.json
                   codex_task.md
                   report.md
        """
        print("=" * 60)
        print("[Step 9/9] 保存输出文档...")
        print("=" * 60)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        feature_name = self._extract_feature_name(raw_requirement)
        folder_name = f"{feature_name}_{timestamp}"

        base_dir = Path(output_dir) if output_dir else Path.cwd() / "output"
        run_dir = base_dir / folder_name
        run_dir.mkdir(parents=True, exist_ok=True)

        self._write_text(run_dir / "raw_requirement.txt", raw_requirement)
        self._write_text(run_dir / "boosted_requirement.md", self.boosted_text)
        self._write_json(run_dir / "fields.json", self.fields)
        self._write_text(run_dir / "test_plan.md", self._build_test_plan_markdown())
        self._write_text(run_dir / "test_cases.md", self._build_cases_markdown())
        self._write_json(run_dir / "test_cases.json", self.test_cases)
        self._write_json(run_dir / "automation_request.json", self.automation_request)
        self._write_json(run_dir / "execution_request.json", self.execution_request)
        self._write_json(run_dir / "review_result.json", self.review_result)
        self._write_text(run_dir / "review_notes.md", self._build_review_notes_markdown())
        self._write_json(run_dir / "project_context_request.json", self.project_context_request)
        self._write_json(run_dir / "codex_task.json", self.codex_task)
        self._write_text(run_dir / "codex_task.md", self._build_codex_task_markdown())
        self._write_text(run_dir / "report.md", self._build_report_markdown(raw_requirement))
        self._write_text(run_dir / "index.html", self._build_html_viewer(run_dir))

        print(f"\n输出目录: {run_dir}/")
        print("  ├── raw_requirement.txt")
        print("  ├── index.html")
        print("  ├── boosted_requirement.md")
        print("  ├── fields.json")
        print("  ├── test_plan.md")
        print("  ├── test_cases.md")
        print("  ├── test_cases.json")
        print("  ├── automation_request.json")
        print("  ├── execution_request.json")
        print("  ├── review_result.json")
        print("  ├── review_notes.md")
        print("  ├── project_context_request.json")
        print("  ├── codex_task.json")
        print("  ├── codex_task.md")
        print("  └── report.md")
        return str(run_dir)

    # ═══ Helpers ═══
    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _build_html_viewer(self, run_dir: Path) -> str:
        """Build an offline HTML viewer for generated Markdown and JSON artifacts."""
        files = [
            ("报告", "report.md"),
            ("测试方案", "test_plan.md"),
            ("测试用例", "test_cases.md"),
            ("审查说明", "review_notes.md"),
            ("Codex 交接", "codex_task.md"),
            ("增强需求", "boosted_requirement.md"),
            ("原始需求", "raw_requirement.txt"),
            ("结构化字段", "fields.json"),
            ("结构化用例", "test_cases.json"),
            ("实现请求", "automation_request.json"),
            ("执行请求", "execution_request.json"),
            ("审查结果", "review_result.json"),
            ("项目上下文请求", "project_context_request.json"),
            ("Codex 任务 JSON", "codex_task.json"),
        ]

        nav_items = []
        sections = []
        for index, (title, filename) in enumerate(files, start=1):
            path = run_dir / filename
            if not path.exists():
                continue
            section_id = f"doc-{index}"
            nav_items.append(
                f'<a href="#{section_id}"><span>{html.escape(title)}</span><small>{html.escape(filename)}</small></a>'
            )
            content = path.read_text(encoding="utf-8")
            if filename.endswith(".json"):
                body = f"<pre><code>{html.escape(content)}</code></pre>"
            else:
                body = self._render_markdown_fragment(content)
            sections.append(
                f"""
                <section id="{section_id}" class="doc-section">
                  <div class="doc-heading">
                    <p>{html.escape(filename)}</p>
                    <h2>{html.escape(title)}</h2>
                  </div>
                  <div class="doc-body">{body}</div>
                </section>
                """
            )

        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>auto-test-flow 产物查看器</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f6f7f9;
      --panel: #ffffff;
      --text: #1f2937;
      --muted: #6b7280;
      --line: #d8dee8;
      --accent: #1769aa;
      --accent-soft: #e8f2fb;
      --code: #0f172a;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", "Microsoft YaHei", Arial, sans-serif;
      line-height: 1.65;
      color: var(--text);
      background: var(--bg);
    }}
    .layout {{
      display: grid;
      grid-template-columns: 280px minmax(0, 1fr);
      min-height: 100vh;
    }}
    nav {{
      position: sticky;
      top: 0;
      height: 100vh;
      overflow-y: auto;
      padding: 24px 18px;
      border-right: 1px solid var(--line);
      background: #eef2f7;
    }}
    nav h1 {{
      margin: 0 0 18px;
      font-size: 20px;
      line-height: 1.3;
    }}
    nav a {{
      display: block;
      padding: 10px 12px;
      margin: 4px 0;
      color: var(--text);
      text-decoration: none;
      border-radius: 8px;
    }}
    nav a:hover {{ background: var(--accent-soft); }}
    nav span {{ display: block; font-weight: 650; }}
    nav small {{ display: block; color: var(--muted); font-size: 12px; }}
    main {{
      width: min(1180px, 100%);
      padding: 28px;
    }}
    .doc-section {{
      margin: 0 0 28px;
      padding: 28px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
    }}
    .doc-heading {{
      margin-bottom: 22px;
      padding-bottom: 14px;
      border-bottom: 1px solid var(--line);
    }}
    .doc-heading p {{
      margin: 0 0 4px;
      color: var(--muted);
      font-size: 13px;
    }}
    .doc-heading h2 {{
      margin: 0;
      font-size: 26px;
      line-height: 1.25;
    }}
    h1, h2, h3, h4 {{ line-height: 1.35; }}
    .doc-body h1 {{ font-size: 26px; margin: 24px 0 12px; }}
    .doc-body h2 {{ font-size: 22px; margin: 22px 0 10px; }}
    .doc-body h3 {{ font-size: 18px; margin: 18px 0 8px; }}
    .doc-body h4 {{ font-size: 16px; margin: 16px 0 6px; }}
    p {{ margin: 10px 0; }}
    ul, ol {{ padding-left: 24px; }}
    li {{ margin: 5px 0; }}
    table {{
      width: 100%;
      margin: 16px 0;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      vertical-align: top;
      padding: 10px 12px;
      border: 1px solid var(--line);
    }}
    th {{
      background: #f1f5f9;
      text-align: left;
      font-weight: 700;
    }}
    code {{
      font-family: Consolas, "Cascadia Mono", monospace;
      color: var(--code);
      background: #eef2f7;
      border-radius: 4px;
      padding: 0 4px;
    }}
    pre {{
      overflow-x: auto;
      padding: 16px;
      background: #111827;
      color: #f9fafb;
      border-radius: 8px;
      line-height: 1.5;
    }}
    pre code {{
      color: inherit;
      background: transparent;
      padding: 0;
    }}
    blockquote {{
      margin: 14px 0;
      padding: 8px 16px;
      color: var(--muted);
      border-left: 4px solid var(--accent);
      background: #f8fafc;
    }}
    @media (max-width: 860px) {{
      .layout {{ display: block; }}
      nav {{
        position: static;
        height: auto;
        border-right: 0;
        border-bottom: 1px solid var(--line);
      }}
      main {{ padding: 16px; }}
      .doc-section {{ padding: 18px; }}
    }}
  </style>
</head>
<body>
  <div class="layout">
    <nav>
      <h1>auto-test-flow 产物</h1>
      {''.join(nav_items)}
    </nav>
    <main>
      {''.join(sections)}
    </main>
  </div>
</body>
</html>
"""

    def _render_markdown_fragment(self, text: str) -> str:
        lines = text.splitlines()
        html_parts = []
        paragraph = []
        list_stack = []
        in_code = False
        code_lines = []
        i = 0

        def flush_paragraph():
            if paragraph:
                html_parts.append(f"<p>{self._render_inline(' '.join(paragraph))}</p>")
                paragraph.clear()

        def close_lists():
            while list_stack:
                html_parts.append(f"</{list_stack.pop()}>")

        while i < len(lines):
            line = lines[i]
            stripped = line.strip()

            if stripped.startswith("```"):
                flush_paragraph()
                close_lists()
                if in_code:
                    html_parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                    code_lines = []
                    in_code = False
                else:
                    in_code = True
                i += 1
                continue

            if in_code:
                code_lines.append(line)
                i += 1
                continue

            if not stripped:
                flush_paragraph()
                close_lists()
                i += 1
                continue

            if stripped.startswith("|") and i + 1 < len(lines) and self._is_markdown_table_separator(lines[i + 1]):
                flush_paragraph()
                close_lists()
                table_lines = [stripped, lines[i + 1].strip()]
                i += 2
                while i < len(lines) and lines[i].strip().startswith("|"):
                    table_lines.append(lines[i].strip())
                    i += 1
                html_parts.append(self._render_table(table_lines))
                continue

            heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
            if heading:
                flush_paragraph()
                close_lists()
                level = len(heading.group(1))
                html_parts.append(f"<h{level}>{self._render_inline(heading.group(2))}</h{level}>")
                i += 1
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                close_lists()
                html_parts.append(f"<blockquote>{self._render_inline(stripped.lstrip('> ').strip())}</blockquote>")
                i += 1
                continue

            unordered = re.match(r"^[-*]\s+(.+)$", stripped)
            ordered = re.match(r"^\d+\.\s+(.+)$", stripped)
            if unordered or ordered:
                flush_paragraph()
                tag = "ul" if unordered else "ol"
                if not list_stack or list_stack[-1] != tag:
                    close_lists()
                    list_stack.append(tag)
                    html_parts.append(f"<{tag}>")
                item = unordered.group(1) if unordered else ordered.group(1)
                html_parts.append(f"<li>{self._render_inline(item)}</li>")
                i += 1
                continue

            close_lists()
            paragraph.append(stripped)
            i += 1

        flush_paragraph()
        close_lists()
        if in_code:
            html_parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
        return "\n".join(html_parts)

    @staticmethod
    def _is_markdown_table_separator(line: str) -> bool:
        return bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line))

    def _render_table(self, lines: list[str]) -> str:
        header = self._split_table_row(lines[0])
        body_rows = [self._split_table_row(line) for line in lines[2:]]
        header_html = "".join(f"<th>{self._render_inline(cell)}</th>" for cell in header)
        body_html = []
        for row in body_rows:
            cells = row + [""] * max(0, len(header) - len(row))
            body_html.append("<tr>" + "".join(f"<td>{self._render_inline(cell)}</td>" for cell in cells[:len(header)]) + "</tr>")
        return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_html)}</tbody></table>"

    @staticmethod
    def _split_table_row(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    @staticmethod
    def _render_inline(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = escaped.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("&lt;br /&gt;", "<br>")
        return escaped

    @staticmethod
    def _extract_feature_name(text: str, max_len: int = 20) -> str:
        """Extract a short feature name from the raw requirement."""
        name = text.strip().split("\n")[0].strip()
        for prefix in ["测试", "请", "帮我", "我要", "需要"]:
            if name.startswith(prefix):
                name = name[len(prefix):]
        name = re.sub(r"[^\u4e00-\u9fff\w]", "_", name)
        return name[:max_len].strip("_") or "test_plan"

    def _create_boost_review_dir(self, raw_requirement: str, output_dir: str = None) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        feature_name = self._extract_feature_name(raw_requirement)
        base_dir = Path(output_dir) if output_dir else Path.cwd() / "output"
        review_dir = base_dir / f"_boost_review_{feature_name}_{timestamp}"
        review_dir.mkdir(parents=True, exist_ok=True)
        return review_dir

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
        """Build user prompt from fields dict for test plan generation."""
        return f"""请根据以下信息生成测试方案：

【测试对象】
{self._fmt(fields.get('test_object'))}

【业务背景】
{self._fmt(fields.get('business_context'))}

【核心需求】
{self._fmt(fields.get('core_requirements'))}

【用户角色】
{self._fmt(fields.get('user_roles'))}

【输入条件】
{self._fmt(fields.get('input_conditions'))}

【预期结果】
{self._fmt(fields.get('expected_results'))}

【异常情况】
{self._fmt(fields.get('exception_scenarios'))}"""

    def _build_test_cases_user_prompt(self) -> str:
        return f"""请基于以下结构化字段和测试方案生成结构化测试用例 JSON。

【结构化字段】
{self._json_block(self.fields)}

【测试方案】
{self.test_plan}
"""

    def _build_automation_request_prompt(self) -> str:
        return f"""请基于以下信息生成自动化脚本实现请求 JSON。注意：只生成请求，不输出代码。

【结构化字段】
{self._json_block(self.fields)}

【测试方案】
{self.test_plan}

【结构化测试用例】
{self._json_block(self.test_cases)}
"""

    def _build_execution_request_prompt(self) -> str:
        return f"""请基于以下信息生成测试执行请求 JSON。注意：只生成执行计划，不真正执行测试。

【结构化字段】
{self._json_block(self.fields)}

【结构化测试用例】
{self._json_block(self.test_cases)}

【自动化脚本实现请求】
{self._json_block(self.automation_request)}
"""

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
            "review_result": "review_result.json",
            "review_notes": "review_notes.md",
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
                    "需求增强",
                    "字段抽取",
                    "测试方案生成",
                    "测试用例结构化",
                    "自动化实现请求生成",
                    "执行请求生成",
                ],
                "codex_gpt_55": [
                    "读取项目上下文",
                    "制定代码修改计划",
                    "等待用户确认",
                    "修改自动化测试代码",
                    "运行测试并修复脚本问题",
                    "输出执行报告",
                ],
            },
            "source_artifacts": {
                "raw_requirement": "raw_requirement.txt",
                "boosted_requirement": "boosted_requirement.md",
                "fields": "fields.json",
                "test_plan": "test_plan.md",
                "test_cases_markdown": "test_cases.md",
                "test_cases_json": "test_cases.json",
                "automation_request": "automation_request.json",
                "execution_request": "execution_request.json",
                "project_context_request": "project_context_request.json",
            },
            "selected_cases": selected_cases,
            "project_context_request": self.project_context_request,
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
                ],
            },
            "implementation_constraints": [
                "优先复用已有 page object、selector、fixture 和 testcase 结构。",
                "新增选择器优先补充到已有 selector 类中。",
                "新增测试用例放到项目既有 testcases 目录中。",
                "测试用例只编排流程和断言，页面细节放到 page object。",
                "不引入新测试框架，除非项目没有合适框架且用户确认。",
            ],
            "next_agent_prompt_file": "codex_task.md",
        }

    def _build_codex_task_markdown(self) -> str:
        """Build the human-readable Codex handoff prompt."""
        if self.codex_task.get("status") == "skipped_by_review_gate":
            return f"""# Codex Handoff Task

Codex handoff was skipped by the review gate.

## 原因

{self.codex_task.get('reason', 'Review gate blocked handoff.')}

## 下一步

1. 读取 `review_notes.md`。
2. 根据审查建议确认或调整 `test_cases.json`、`automation_request.json`、`execution_request.json`。
3. 如确认可以继续，再使用 `--review-policy ask` 或 `--review-policy full-auto` 重新运行。
"""

        return f"""# Codex Handoff Task

{CODEX_HANDOFF_REQUIREMENTS}

## 任务目标

请基于本目录中的 Phase 2 产物接管自动化测试代码落地。DeepSeek 已完成测试设计和结构化分析；Codex/GPT-5.5 负责读取项目、提出修改计划、等待用户确认、修改代码、运行测试并修复脚本问题。

## 必读产物

- `test_cases.json`
- `automation_request.json`
- `execution_request.json`
- `project_context_request.json`
- `test_plan.md`
- `test_cases.md`

## 修改前确认 Gate

在任何代码修改前，必须先向用户说明：

1. 准备修改的文件
2. 每个文件的修改点
3. 修改原因
4. 预计运行的验证命令
5. 环境和测试数据安全判断

只有用户明确说“可以修改”“确认修改”“按这个改”后，才能修改代码文件。

## DeepSeek 与 Codex 职责边界

- DeepSeek 负责：需求增强、字段抽取、测试方案、结构化用例、自动化实现请求、执行请求。
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

1. 读取 `project_context_request.json`。
2. 在目标项目中执行只读发现，确认测试框架、目录结构、页面对象、选择器、fixtures 和运行命令。
3. 基于 `test_cases.json` 选择首批 P0/P1 自动化候选用例。
4. 输出代码修改计划并等待用户确认。
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

- 已保存原始需求和增强后的需求描述。
- 已抽取结构化字段。
- 已生成测试方案。
- 已生成独立的结构化测试用例。
- 已生成自动化脚本实现请求，作为后续项目代码生成节点的输入。
- 已生成测试执行请求，作为后续执行和报告节点的输入。
- 已完成自动审查，并生成 review_result.json 与 review_notes.md。
- 已生成 Codex handoff 任务包，作为 GPT-5.5 代码落地阶段的输入。

## 产物清单

- `raw_requirement.txt`
- `boosted_requirement.md`
- `fields.json`
- `test_plan.md`
- `test_cases.md`
- `test_cases.json`
- `automation_request.json`
- `execution_request.json`
- `review_result.json`
- `review_notes.md`
- `project_context_request.json`
- `codex_task.json`
- `codex_task.md`
- `report.md`

## 当前边界

- 本阶段不直接修改被测项目代码。
- 本阶段不真实执行测试。
- 自动化代码生成、项目发现、测试执行和失败修复由 Codex handoff 阶段接入。
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
            skip_boost: bool = False, review_policy: str = "auto-review") -> str:
        """Execute the full Phase 2.6 pipeline."""
        if review_policy not in REVIEW_POLICIES:
            raise ValueError(f"Unsupported review policy: {review_policy}")
        self.review_policy = review_policy

        print(f"\n{'=' * 60}")
        print("  自动化测试工作流 (Phase 2.6)")
        print(f"  审查策略: {self.review_policy}")
        print(f"  输入: {raw_requirement[:50]}{'...' if len(raw_requirement) > 50 else ''}")
        print(f"{'=' * 60}\n")

        try:
            if not skip_boost:
                self.step_boost(raw_requirement)
                self.step_review_boosted_requirement(raw_requirement, output_dir)
            else:
                self.boosted_text = raw_requirement

            self.step_extract_fields()
            self.step_generate_test_plan()
            self.step_generate_test_cases()
            self.step_build_automation_request()
            self.step_build_execution_request()
            self.step_review_gate()
            if self._should_build_codex_handoff():
                self.step_build_codex_handoff()
            else:
                self._set_skipped_codex_handoff()
            result_dir = self.step_save_output(raw_requirement, output_dir)

            print(f"\n{'=' * 60}")
            print("  工作流完成!")
            print(f"{'=' * 60}")
            return result_dir

        except Exception as e:
            print(f"\n[ERROR] 工作流执行失败: {e}", file=sys.stderr)
            raise


def main():
    parser = argparse.ArgumentParser(
        description="自动化测试工作流编排器 - Phase 2.6",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python orchestrator.py "对登录页面进行功能测试"
  python orchestrator.py --file requirements.txt
  python orchestrator.py "测试注册功能" --output-dir ./output
  python orchestrator.py --file req.txt --skip-boost
  python orchestrator.py "测试登录功能" --review-policy ask
        """,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("requirement", nargs="?", help="测试需求描述")
    group.add_argument("-f", "--file", help="从文件读取测试需求")

    parser.add_argument("-o", "--output-dir", default=None,
                        help="输出根目录(可选，默认当前目录下的 output/)")
    parser.add_argument("--skip-boost", action="store_true",
                        help="跳过提示词优化步骤")
    parser.add_argument("--review-policy", choices=REVIEW_POLICIES,
                        default="auto-review",
                        help="审查策略: ask=命令行确认, auto-review=自动审查并阻塞高风险, full-auto=不阻塞")

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
        skip_boost=args.skip_boost,
        review_policy=args.review_policy,
    )


if __name__ == "__main__":
    main()
