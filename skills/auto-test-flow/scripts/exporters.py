#!/usr/bin/env python3
"""Export generated test cases to spreadsheet and mind-map formats."""

import html
import json
import zipfile
from pathlib import Path


def write_test_cases_xlsx(path: Path, test_cases: dict) -> None:
    """Write test cases as a dependency-free Excel workbook."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = _test_case_export_rows(test_cases)
    sheet_data = []
    for row_index, row in enumerate(rows, start=1):
        cells = []
        for col_index, value in enumerate(row, start=1):
            cell_ref = f"{_xlsx_col_name(col_index)}{row_index}"
            text = _xml_text(value)
            cells.append(
                f'<c r="{cell_ref}" t="inlineStr"><is><t>{text}</t></is></c>'
            )
        sheet_data.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <cols>
<col min="1" max="1" width="16" customWidth="1"/>
<col min="2" max="2" width="28" customWidth="1"/>
<col min="3" max="5" width="14" customWidth="1"/>
<col min="6" max="9" width="36" customWidth="1"/>
  </cols>
  <sheetData>
{''.join(sheet_data)}
  </sheetData>
</worksheet>"""
    workbook = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="测试用例" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)

def write_test_cases_xmind(path: Path, test_cases: dict) -> None:
    """Write test cases as a simple XMind 2020 compatible workbook."""
    path.parent.mkdir(parents=True, exist_ok=True)
    feature = str(test_cases.get("feature") or "自动化测试用例")
    attached = []
    for index, case in enumerate(test_cases.get("cases", []), start=1):
        title = f"{case.get('id', f'TC_{index:03d}')} {case.get('title', '')}".strip()
        children = []
        for label, key in [
            ("前置条件", "preconditions"),
            ("测试步骤", "steps"),
            ("测试数据", "test_data"),
            ("预期结果", "expected_result"),
        ]:
            children.append({
                "id": f"case-{index}-{key}",
                "class": "topic",
                "title": f"{label}: {_plain_text(case.get(key))}",
            })
        attached.append({
            "id": f"case-{index}",
            "class": "topic",
            "title": title,
            "labels": [str(case.get("priority") or ""), str(case.get("type") or "")],
            "children": {"attached": children},
        })
    content = [{
        "id": "sheet-1",
        "class": "sheet",
        "title": feature,
        "rootTopic": {
            "id": "root",
            "class": "topic",
            "title": feature,
            "children": {"attached": attached},
        },
    }]
    metadata = {
        "creator": {"name": "auto-test-flow"},
        "activeSheetId": "sheet-1",
    }
    manifest = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
        }
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False, indent=2))
        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False, indent=2))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

def _test_case_export_rows(test_cases: dict) -> list[list[str]]:
    rows = [[
        "用例编号",
        "用例标题",
        "优先级",
        "类型",
        "自动化候选",
        "前置条件",
        "测试步骤",
        "测试数据",
        "预期结果",
    ]]
    for case in test_cases.get("cases", []):
        rows.append([
            _plain_text(case.get("id")),
            _plain_text(case.get("title")),
            _plain_text(case.get("priority")),
            _plain_text(case.get("type")),
            _plain_text(case.get("automation_candidate")),
            _plain_text(case.get("preconditions")),
            _plain_text(case.get("steps")),
            _plain_text(case.get("test_data")),
            _plain_text(case.get("expected_result")),
        ])
    return rows

def _plain_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if isinstance(value, dict):
        return "\n".join(f"{key}: {val}" for key, val in value.items())
    return str(value)

def _xlsx_col_name(index: int) -> str:
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name

def _xml_text(value) -> str:
    return html.escape(str(value), quote=False)
