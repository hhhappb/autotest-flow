#!/usr/bin/env python3
"""Build the static HTML viewer for generated auto-test-flow artifacts."""

import html
import re
from pathlib import Path


def build_html_viewer(run_dir: Path) -> str:
    """Build an offline HTML viewer for generated Markdown and JSON artifacts."""
    files = [
        ("报告", "md/report.md", "markdown"),
        ("需求上下文", "md/requirement.md", "markdown"),
        ("测试方案", "md/test_plan.md", "markdown"),
        ("测试用例", "md/test_cases.md", "markdown"),
        ("审查说明", "md/review_notes.md", "markdown"),
        ("Excel 用例", "exports/test_cases.xlsx", "download"),
        ("XMind 用例", "exports/test_cases.xmind", "download"),
    ]

    nav_items = []
    sections = []
    for index, (title, filename, kind) in enumerate(files, start=1):
        path = run_dir / filename
        if not path.exists():
            continue
        section_id = f"doc-{index}"
        nav_items.append(
            f'<a href="#{section_id}"><span>{html.escape(title)}</span><small>{html.escape(filename)}</small></a>'
        )
        if kind == "download":
            body = (
                '<div class="download-card">'
                f'<p>{html.escape(filename)}</p>'
                f'<a href="{html.escape(filename)}" download>下载 {html.escape(title)}</a>'
                '</div>'
            )
        else:
            content = path.read_text(encoding="utf-8")
            body = render_markdown_fragment(content)
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
  <title>测试产物查看器</title>
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
.download-card {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 20px;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: #f8fbff;
}}
.download-card p {{
  margin: 0;
  color: var(--muted);
  overflow-wrap: anywhere;
}}
.download-card a {{
  flex: 0 0 auto;
  display: inline-block;
  padding: 8px 14px;
  border-radius: 6px;
  color: #fff;
  background: var(--accent);
  text-decoration: none;
  font-weight: 700;
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
  <h1>测试产物</h1>
  {''.join(nav_items)}
</nav>
<main>
  {''.join(sections)}
</main>
  </div>
</body>
</html>
"""

def render_markdown_fragment(text: str) -> str:
    lines = text.splitlines()
    html_parts = []
    paragraph = []
    list_stack = []
    in_code = False
    code_lines = []
    i = 0

    def flush_paragraph():
        if paragraph:
            html_parts.append(f"<p>{render_inline(' '.join(paragraph))}</p>")
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

        if stripped.startswith("|") and i + 1 < len(lines) and _is_markdown_table_separator(lines[i + 1]):
            flush_paragraph()
            close_lists()
            table_lines = [stripped, lines[i + 1].strip()]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            html_parts.append(_render_table(table_lines))
            continue

        heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            close_lists()
            level = len(heading.group(1))
            html_parts.append(f"<h{level}>{render_inline(heading.group(2))}</h{level}>")
            i += 1
            continue

        if stripped.startswith(">"):
            flush_paragraph()
            close_lists()
            html_parts.append(f"<blockquote>{render_inline(stripped.lstrip('> ').strip())}</blockquote>")
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
            html_parts.append(f"<li>{render_inline(item)}</li>")
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

def _is_markdown_table_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$", line))

def _render_table(lines: list[str]) -> str:
    header = _split_table_row(lines[0])
    body_rows = [_split_table_row(line) for line in lines[2:]]
    header_html = "".join(f"<th>{render_inline(cell)}</th>" for cell in header)
    body_html = []
    for row in body_rows:
        cells = row + [""] * max(0, len(header) - len(row))
        body_html.append("<tr>" + "".join(f"<td>{render_inline(cell)}</td>" for cell in cells[:len(header)]) + "</tr>")
    return f"<table><thead><tr>{header_html}</tr></thead><tbody>{''.join(body_html)}</tbody></table>"

def _split_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]

def render_inline(text: str) -> str:
    escaped = html.escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = escaped.replace("&lt;br&gt;", "<br>").replace("&lt;br/&gt;", "<br>").replace("&lt;br /&gt;", "<br>")
    return escaped
