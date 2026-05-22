#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Local web workbench for auto-test-flow.

The workbench is intentionally dependency-free. It serves a small local web UI,
runs the existing orchestrator, and can hand a generated codex_task.md to
`codex.cmd exec` while keeping logs beside the generated run artifacts.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import io
import json
import mimetypes
import os
import posixpath
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zipfile
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree


FINAL_STATUSES = {"success", "failed"}
REVIEW_POLICIES = {"auto-review", "full-auto"}
APPROVAL_POLICIES = {"on-request", "never"}
DEEPSEEK_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
TEXT_EXTENSIONS = {".txt", ".md", ".csv"}
SPREADSHEET_EXTENSIONS = {".xlsx", ".xls"}
SUPPORTED_ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | TEXT_EXTENSIONS | SPREADSHEET_EXTENSIONS


class WorkbenchState:
    """Shared state for the local HTTP handler."""

    def __init__(
        self,
        host: str,
        port: int,
        orchestrator_path: Path,
        output_dir: Path,
        project_root: Path,
        python_executable: str,
        codex_command: str,
        model: str = "deepseek-v4-flash",
    ) -> None:
        self.host = host
        self.port = port
        self.orchestrator_path = orchestrator_path.resolve()
        self.output_dir = output_dir.resolve()
        self.project_root = project_root.resolve()
        self.python_executable = python_executable
        self.codex_command = codex_command
        self.model = model
        self.jobs: dict[str, dict] = {}
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def create_job(self, kind: str) -> dict:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "command": [],
            "logs": [],
            "run_dir": None,
            "run_url": None,
            "log_file": None,
            "last_message_file": None,
            "summary_file": None,
            "exit_code": None,
            "error": None,
            "interactive": False,
        }
        with self.lock:
            self.jobs[job_id] = job
        return job

    def append_log(self, job_id: str, line: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["logs"].append(line)
            job["logs"] = job["logs"][-3000:]
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def update_job(self, job_id: str, **fields) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(fields)
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def get_job(self, job_id: str) -> dict | None:
        with self.lock:
            job = self.jobs.get(job_id)
            return json.loads(json.dumps(job, ensure_ascii=False)) if job else None

    def attach_process(self, job_id: str, process: subprocess.Popen) -> None:
        with self.lock:
            self.processes[job_id] = process

    def detach_process(self, job_id: str) -> None:
        with self.lock:
            self.processes.pop(job_id, None)
            if job_id in self.jobs:
                self.jobs[job_id]["interactive"] = False

    def send_input(self, job_id: str, text: str) -> dict:
        with self.lock:
            job = self.jobs.get(job_id)
            process = self.processes.get(job_id)
            if not job or not process or process.stdin is None or process.poll() is not None:
                raise ValueError("No running interactive process for this job")
            process.stdin.write(text.rstrip("\n") + "\n")
            process.stdin.flush()
            job["logs"].append(f">>> {text.rstrip()}")
            job["updated_at"] = datetime.now().isoformat(timespec="seconds")
            return json.loads(json.dumps(job, ensure_ascii=False))

    def list_runs(self) -> list[dict]:
        runs = []
        for path in sorted(self.output_dir.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
            if not path.is_dir() or not (path / "index.html").exists():
                continue
            review = self._read_run_review(path)
            codex = self._read_codex_status(path)
            runs.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "url": f"/runs/{quote(path.name)}/index.html",
                    "review_url": f"/runs/{quote(path.name)}/index.html#doc-md-review-notes-md",
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    "review_decision": review.get("decision"),
                    "review_summary": review.get("summary"),
                    "review_counts": review.get("counts", {}),
                    "codex_status": codex.get("status"),
                    "codex_exit_code": codex.get("exit_code"),
                    "codex_updated_at": codex.get("updated_at"),
                    "codex_summary_url": codex.get("summary_url"),
                    "codex_log_url": codex.get("log_url"),
                    "codex_last_message_url": codex.get("last_message_url"),
                }
            )
        return runs[:50]

    def delete_run(self, run_ref: str) -> None:
        run_dir = self.resolve_run_dir(run_ref)
        output_root = self.output_dir.resolve()
        if run_dir == output_root or output_root not in run_dir.parents:
            raise ValueError("run_dir must be inside the configured output directory")
        shutil.rmtree(run_dir)

    @staticmethod
    def _read_run_review(run_dir: Path) -> dict:
        review_path = run_dir / "json" / "review_result.json"
        if review_path.exists():
            try:
                payload = json.loads(review_path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                return {}

        codex_task = run_dir / "md" / "codex_task.md"
        if codex_task.exists():
            try:
                text = codex_task.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                text = ""
            if "skipped by the review gate" in text:
                return {
                    "decision": "blocked",
                    "summary": "Codex handoff was skipped by the review gate.",
                    "counts": {},
                }
        return {}

    @staticmethod
    def _read_codex_status(run_dir: Path) -> dict:
        logs_dir = run_dir / "logs"
        if not logs_dir.exists():
            return {"status": "not_started"}

        decision_files = sorted(logs_dir.glob("codex_decision_*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        exec_files = sorted(logs_dir.glob("codex_exec_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        last_message_files = sorted(
            logs_dir.glob("codex_last_message_*.md"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not decision_files and not exec_files and not last_message_files:
            return {"status": "not_started"}

        def codex_job_id(path: Path) -> str:
            stem = path.stem
            for prefix in ("codex_decision_", "codex_exec_", "codex_last_message_"):
                if stem.startswith(prefix):
                    return stem[len(prefix):]
            return ""

        latest_file = max(
            [*decision_files, *exec_files, *last_message_files],
            key=lambda p: p.stat().st_mtime,
        )
        current_job_id = codex_job_id(latest_file)
        if not current_job_id:
            return {"status": "unknown"}

        decision_file = logs_dir / f"codex_decision_{current_job_id}.md"
        exec_file = logs_dir / f"codex_exec_{current_job_id}.log"
        last_message_file = logs_dir / f"codex_last_message_{current_job_id}.md"
        decision_file = decision_file if decision_file.exists() else None
        exec_file = exec_file if exec_file.exists() else None
        last_message_file = last_message_file if last_message_file.exists() else None

        exit_code = None
        if decision_file:
            try:
                decision_text = decision_file.read_text(encoding="utf-8", errors="replace")
                match = re.search(r"(?:退出码|Exit code)\s*[:：]\s*(-?\d+)", decision_text)
                if match:
                    exit_code = int(match.group(1))
            except OSError:
                exit_code = None
        elif exec_file:
            try:
                tail_text = exec_file.read_text(encoding="utf-8", errors="replace")[-4000:]
                match = re.search(r"Exit code\s*[:：]\s*(-?\d+)", tail_text)
                if match:
                    exit_code = int(match.group(1))
            except OSError:
                exit_code = None

        if exit_code == 0:
            status = "success"
        elif exit_code is None:
            status = "unknown"
        else:
            status = "failed"

        latest_files = [path for path in (decision_file, exec_file, last_message_file) if path and path.exists()]
        latest_mtime = max((path.stat().st_mtime for path in latest_files), default=None)

        def log_url(path: Path | None) -> str | None:
            if not path:
                return None
            return f"/runs/{quote(run_dir.name)}/logs/{quote(path.name)}"

        return {
            "status": status,
            "exit_code": exit_code,
            "updated_at": datetime.fromtimestamp(latest_mtime).isoformat(timespec="seconds") if latest_mtime else None,
            "summary_url": log_url(decision_file),
            "log_url": log_url(exec_file),
            "last_message_url": log_url(last_message_file),
        }

    def resolve_run_dir(self, run_ref: str) -> Path:
        candidate = Path(run_ref)
        if not candidate.is_absolute():
            candidate = self.output_dir / run_ref
        candidate = candidate.resolve()
        output_root = self.output_dir.resolve()
        if candidate != output_root and output_root not in candidate.parents:
            raise ValueError("run_dir must be inside the configured output directory")
        if not candidate.exists() or not candidate.is_dir():
            raise FileNotFoundError(f"Run folder does not exist: {candidate}")
        if not (candidate / "index.html").exists():
            raise FileNotFoundError(f"Run folder has no index.html: {candidate}")
        return candidate

    def update_settings(self, project_root: Path, output_dir: Path, model: str | None = None) -> None:
        self.project_root = project_root.resolve()
        self.output_dir = output_dir.resolve()
        if model:
            self.model = model
        self.output_dir.mkdir(parents=True, exist_ok=True)


def run_command(
    state: WorkbenchState,
    job_id: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
    input_text: str | None = None,
    interactive: bool = False,
    extra_env: dict[str, str] | None = None,
    stream_output_to_job: bool = True,
) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    env.setdefault("NO_COLOR", "1")
    if extra_env:
        env.update(extra_env)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    state.update_job(job_id, command=command, log_file=str(log_path), interactive=interactive)

    with log_path.open("w", encoding="utf-8", errors="replace") as log_file:
        log_file.write(f"$ {' '.join(command)}\n\n")
        log_file.flush()
        state.append_log(job_id, f"$ {' '.join(command)}")

        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdin=subprocess.PIPE if input_text is not None or interactive else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if process.stdin is not None:
            state.attach_process(job_id, process)

        try:
            writer = None
            if input_text is not None:
                writer = threading.Thread(target=_write_stdin, args=(process, input_text), daemon=True)
                writer.start()

            assert process.stdout is not None
            for raw_line in process.stdout:
                line = _repair_mojibake(raw_line.rstrip("\n"))
                log_file.write(line + "\n")
                log_file.flush()
                if stream_output_to_job:
                    state.append_log(job_id, line)

            if writer:
                writer.join(timeout=5)
            exit_code = process.wait()
            state.append_log(job_id, f"Exit code: {exit_code}")
            log_file.write(f"\nExit code: {exit_code}\n")
            return exit_code
        finally:
            state.detach_process(job_id)


def _repair_mojibake(text: str) -> str:
    markers = ("浣", "鐨", "鏄", "涓", "鍦", "瀹", "闇", "銆", "锛", "鈥")
    if not text or not any(marker in text for marker in markers):
        return text
    repaired = text.encode("gbk", errors="replace").decode("utf-8", errors="replace")
    return repaired if _looks_more_readable(repaired, text) else text


def _looks_more_readable(candidate: str, original: str) -> bool:
    common_words = ("你", "现在", "本地", "工作台", "调用", "项目", "目录", "需求", "测试", "修改", "确认")
    candidate_hits = sum(word in candidate for word in common_words)
    original_hits = sum(word in original for word in common_words)
    if candidate_hits > original_hits:
        return True
    cjk = re.compile(r"[\u4e00-\u9fff]")
    candidate_score = len(cjk.findall(candidate)) - candidate.count("�")
    original_score = len(cjk.findall(original)) - original.count("�")
    return candidate_score >= original_score


def _write_stdin(process: subprocess.Popen, input_text: str) -> None:
    if process.stdin is None:
        return
    try:
        process.stdin.write(input_text)
        process.stdin.flush()
    finally:
        process.stdin.close()


def prepare_input_materials(
    state: WorkbenchState,
    job_id: str,
    requirement: str,
    attachments: list[dict],
    project_root: Path,
) -> tuple[str, list[dict]]:
    """Save uploaded materials and build text that orchestrator can consume."""
    materials_dir = state.output_dir / "_workbench_uploads" / job_id
    materials_dir.mkdir(parents=True, exist_ok=True)
    materials = []

    for index, attachment in enumerate(attachments, start=1):
        original_name = str(attachment.get("name") or f"attachment_{index}").strip()
        safe_name = _safe_filename(original_name, index)
        extension = Path(safe_name).suffix.lower()
        if extension not in SUPPORTED_ATTACHMENT_EXTENSIONS:
            raise ValueError(f"Unsupported attachment type: {original_name}")

        data_url = str(attachment.get("data") or "")
        encoded = data_url.split(",", 1)[1] if "," in data_url else data_url
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:  # noqa: BLE001 - user-facing validation.
            raise ValueError(f"Invalid attachment data: {original_name}") from exc
        if len(content) > 25 * 1024 * 1024:
            raise ValueError(f"Attachment is too large: {original_name}")

        target_path = materials_dir / safe_name
        target_path.write_bytes(content)
        material = {
            "name": original_name,
            "saved_name": safe_name,
            "path": str(target_path),
            "extension": extension,
            "size": len(content),
            "kind": _material_kind(extension),
            "summary": _extract_material_summary(target_path, extension),
        }
        materials.append(material)

    combined_requirement = _build_combined_requirement(requirement, materials, project_root)
    return combined_requirement, materials


def _safe_filename(name: str, index: int) -> str:
    path_name = Path(name).name or f"attachment_{index}"
    path_name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", path_name)
    stem = Path(path_name).stem or f"attachment_{index}"
    suffix = Path(path_name).suffix.lower()
    stem = stem[:80].strip(" ._") or f"attachment_{index}"
    return f"{index:02d}_{stem}{suffix}"


def _material_kind(extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return "image"
    if extension in SPREADSHEET_EXTENSIONS:
        return "spreadsheet"
    return "text"


def _extract_material_summary(path: Path, extension: str) -> str:
    if extension in IMAGE_EXTENSIONS:
        return "图片材料已保存；生成计划时仅记录路径，Codex 执行时会通过 --image 携带给模型。"
    if extension == ".xlsx":
        return _extract_xlsx_text(path)
    if extension == ".xls":
        return "已保存 .xls 文件；当前标准库工作台不能直接解析二进制 xls，后续交给 Codex/人工按文件路径查看。"
    if extension == ".csv":
        return _extract_csv_text(path)
    return _truncate(path.read_text(encoding="utf-8", errors="replace"), 12000)


def _extract_csv_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    output = io.StringIO()
    reader = csv.reader(io.StringIO(text))
    for row_index, row in enumerate(reader, start=1):
        if row_index > 80:
            output.write("... CSV 内容已截断，仅展示前 80 行。\n")
            break
        output.write(" | ".join(cell.strip() for cell in row[:30]) + "\n")
    return _truncate(output.getvalue(), 12000)


def _extract_xlsx_text(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            shared_strings = _read_xlsx_shared_strings(archive)
            sheet_names = sorted(
                name for name in archive.namelist()
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            lines = []
            for sheet_index, sheet_name in enumerate(sheet_names[:8], start=1):
                lines.append(f"[Sheet {sheet_index}: {sheet_name}]")
                xml_content = archive.read(sheet_name)
                lines.extend(_read_xlsx_sheet_rows(xml_content, shared_strings))
                lines.append("")
            if len(sheet_names) > 8:
                lines.append("... xlsx sheet 数量较多，仅展示前 8 个。")
            return _truncate("\n".join(lines).strip(), 16000)
    except Exception as exc:  # noqa: BLE001 - keep pipeline usable with a note.
        return f"xlsx 内容抽取失败，文件仍已保存，可由 Codex/人工查看。错误：{exc}"


def _read_xlsx_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    try:
        root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings = []
    for item in root.findall("x:si", namespace):
        parts = [node.text or "" for node in item.findall(".//x:t", namespace)]
        strings.append("".join(parts))
    return strings


def _read_xlsx_sheet_rows(xml_content: bytes, shared_strings: list[str]) -> list[str]:
    namespace = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    root = ElementTree.fromstring(xml_content)
    rows = []
    for row_index, row in enumerate(root.findall(".//x:sheetData/x:row", namespace), start=1):
        if row_index > 80:
            rows.append("... sheet 内容已截断，仅展示前 80 行。")
            break
        cells = []
        for cell in row.findall("x:c", namespace)[:30]:
            cells.append(_read_xlsx_cell(cell, shared_strings, namespace).strip())
        if any(cells):
            rows.append(" | ".join(cells))
    return rows


def _read_xlsx_cell(cell: ElementTree.Element, shared_strings: list[str], namespace: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("x:v", namespace)
    if cell_type == "s" and value is not None:
        try:
            return shared_strings[int(value.text or "0")]
        except (ValueError, IndexError):
            return value.text or ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", namespace))
    return value.text if value is not None and value.text is not None else ""


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n... 内容已截断。"


def _build_combined_requirement(requirement: str, materials: list[dict], project_root: Path) -> str:
    parts = ["【项目根目录】\n" + str(project_root)]
    if requirement.strip():
        parts.append("【需求描述】\n" + requirement.strip())
    if materials:
        lines = ["【需求材料】"]
        for material in materials:
            lines.append(
                f"- {material['name']} ({material['kind']}, {material['size']} bytes): {material['path']}"
            )
        parts.append("\n".join(lines))
    return "\n\n".join(parts).strip()


def write_input_materials(run_dir: Path, materials: list[dict], combined_requirement: str) -> None:
    attachments_dir = run_dir / "attachments"
    md_dir = run_dir / "md"
    json_dir = run_dir / "json"
    copied = []
    for material in materials:
        source = Path(material["path"])
        target = attachments_dir / material["saved_name"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.exists():
            shutil.copyfile(source, target)
        item = dict(material)
        item["path"] = str(target)
        item["relative_path"] = f"attachments/{material['saved_name']}"
        copied.append(item)

    payload = {
        "combined_requirement": combined_requirement,
        "materials": copied,
    }
    json_dir.mkdir(parents=True, exist_ok=True)
    (json_dir / "input_materials.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_dir.mkdir(parents=True, exist_ok=True)
    (md_dir / "input_materials.md").write_text(
        _build_input_materials_markdown(copied, combined_requirement),
        encoding="utf-8",
    )


def _build_input_materials_markdown(materials: list[dict], combined_requirement: str) -> str:
    lines = ["# 输入材料", "", "## 合并后的需求输入", "", "```text", combined_requirement, "```", ""]
    lines.append("## 附件")
    if not materials:
        lines.append("")
        lines.append("无附件。")
        return "\n".join(lines)
    for material in materials:
        lines.extend([
            "",
            f"### {material['name']}",
            "",
            f"- 类型: `{material['kind']}`",
            f"- 路径: `{material['relative_path']}`",
            f"- 大小: `{material['size']}` bytes",
        ])
    return "\n".join(lines)


def load_input_materials(run_dir: Path) -> list[dict]:
    path = run_dir / "json" / "input_materials.json"
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload.get("materials", [])


def resolve_project_root(state: WorkbenchState, value) -> Path:
    raw_value = str(value or "").strip()
    candidate = Path(raw_value) if raw_value else state.project_root
    try:
        candidate = candidate.expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"Invalid project root: {candidate}") from exc
    if not candidate.exists() or not candidate.is_dir():
        raise ValueError(f"Project root does not exist or is not a directory: {candidate}")
    return normalize_codex_workspace_root(candidate)


def normalize_codex_workspace_root(candidate: Path) -> Path:
    """Use the nearest ancestor with AGENTS.md as the Codex workspace root."""
    if (candidate / "AGENTS.md").exists():
        return candidate
    for parent in candidate.parents:
        if (parent / "AGENTS.md").exists():
            return parent
    return candidate


def resolve_test_project_root(workspace_root: Path) -> Path:
    """Resolve the runnable auto-test project root without changing the Codex workspace root."""
    root = workspace_root.expanduser().resolve()
    candidates = [root]
    if root.name.lower() != "auto-test":
        candidates.append(root / "auto-test")
    checked = []
    for candidate in candidates:
        candidate = candidate.resolve()
        checked.append(str(candidate / "runner.py"))
        if (candidate / "runner.py").exists():
            return candidate
    raise ValueError("未找到 auto-test runner.py，已检查: " + "；".join(checked))


def normalize_test_path(test_project_root: Path, value: str) -> str:
    raw_value = value.strip()
    if not raw_value:
        raise ValueError("test_path is required")
    path = Path(raw_value)
    if path.is_absolute():
        resolved = path.resolve()
        root = test_project_root.resolve()
        if resolved != root and root not in resolved.parents:
            raise ValueError(f"测试路径必须位于 auto-test 项目内: {resolved}")
        return resolved.relative_to(root).as_posix()

    normalized = raw_value.replace("\\", "/").strip("/")
    parts = PurePosixPath(normalized).parts
    if not normalized or any(part in ("", ".", "..") for part in parts):
        raise ValueError(f"无效测试路径: {raw_value}")
    return normalized


def allure_report_url_for_env(env: str) -> str:
    if env == "all":
        return "/allure/allure_report_test/index.html"
    return "/allure/allure_report/index.html"


def resolve_output_dir(state: WorkbenchState, value) -> Path:
    raw_value = str(value or "").strip()
    candidate = Path(raw_value) if raw_value else state.output_dir
    try:
        candidate = candidate.expanduser().resolve()
    except OSError as exc:
        raise ValueError(f"Invalid output directory: {candidate}") from exc
    if candidate.exists() and not candidate.is_dir():
        raise ValueError(f"Output path exists but is not a directory: {candidate}")
    return candidate


def apply_settings(state: WorkbenchState, payload: dict) -> dict:
    project_root = resolve_project_root(state, payload.get("project_root"))
    output_dir = resolve_output_dir(state, payload.get("output_dir"))
    model = resolve_model(payload.get("model"), state.model)
    state.update_settings(project_root, output_dir, model=model)
    return {
        "project_root": str(state.project_root),
        "output_dir": str(state.output_dir),
        "model": state.model,
    }


def resolve_model(raw_value: object, default: str = "deepseek-v4-flash") -> str:
    value = str(raw_value or default).strip()
    if value not in DEEPSEEK_MODELS:
        raise ValueError(f"model must be one of {sorted(DEEPSEEK_MODELS)}")
    return value


def select_directory(initial_dir: str = "", title: str = "选择目录") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 - surface local desktop limitation.
        raise RuntimeError("Folder picker is unavailable because tkinter cannot be loaded.") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(
            initialdir=initial_dir or None,
            title=title or "选择目录",
            mustexist=True,
        )
    finally:
        root.destroy()
    return selected or ""


def select_file(initial_dir: str = "", title: str = "选择文件") -> str:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 - surface local desktop limitation.
        raise RuntimeError("File picker is unavailable because tkinter cannot be loaded.") from exc

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            initialdir=initial_dir or None,
            title=title or "选择文件",
            filetypes=(("Python tests", "*.py"), ("All files", "*.*")),
        )
    finally:
        root.destroy()
    return selected or ""


def start_generation_job(state: WorkbenchState, payload: dict) -> dict:
    requirement = str(payload.get("requirement", "")).strip()
    attachments = payload.get("attachments") or []
    if not requirement and not attachments:
        raise ValueError("requirement or attachment is required")
    apply_settings(state, payload)
    project_root = resolve_project_root(state, payload.get("project_root"))

    review_policy = str(payload.get("review_policy", "auto-review"))
    if review_policy not in REVIEW_POLICIES:
        raise ValueError(f"review_policy must be one of {sorted(REVIEW_POLICIES)}")
    model = resolve_model(payload.get("model"), state.model)

    job = state.create_job("generate")
    job_id = job["id"]
    combined_requirement, materials = prepare_input_materials(
        state,
        job_id,
        requirement,
        attachments,
        project_root,
    )
    thread = threading.Thread(
        target=_run_generation_job,
        args=(
            state,
            job_id,
            combined_requirement,
            materials,
            review_policy,
            model,
            bool(payload.get("full_artifacts")),
        ),
        daemon=True,
    )
    thread.start()
    return job


def _run_generation_job(
    state: WorkbenchState,
    job_id: str,
    requirement: str,
    materials: list[dict],
    review_policy: str,
    model: str,
    full_artifacts: bool,
) -> None:
    before = _existing_run_dirs(state.output_dir)
    temp_log = state.output_dir / "_workbench_logs" / f"pipeline_{job_id}.log"
    requirement_file = state.output_dir / "_workbench_uploads" / job_id / "combined_requirement.txt"
    requirement_file.parent.mkdir(parents=True, exist_ok=True)
    requirement_file.write_text(requirement, encoding="utf-8")
    command = [
        state.python_executable,
        str(state.orchestrator_path),
        "--file",
        str(requirement_file),
        "--output-dir",
        str(state.output_dir),
        "--review-policy",
        review_policy,
    ]
    if full_artifacts:
        command.append("--full-artifacts")

    try:
        exit_code = run_command(
            state,
            job_id,
            command,
            state.orchestrator_path.parent,
            temp_log,
            interactive=True,
            extra_env={"ANTHROPIC_MODEL": model},
        )
        run_dir = _find_new_run_dir(state.output_dir, before)
        if run_dir:
            write_input_materials(run_dir, materials, requirement)
            review = state._read_run_review(run_dir)
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            final_log = logs_dir / "pipeline.log"
            shutil.copyfile(temp_log, final_log)
            state.update_job(
                job_id,
                run_dir=str(run_dir),
                run_url=f"/runs/{quote(run_dir.name)}/index.html",
                review_url=f"/runs/{quote(run_dir.name)}/index.html#doc-md-review-notes-md",
                review_decision=review.get("decision"),
                review_summary=review.get("summary"),
                review_counts=review.get("counts", {}),
                log_file=str(final_log),
            )
        status = "success" if exit_code == 0 and run_dir else "failed"
        error = None if status == "success" else "Pipeline did not finish successfully or no run folder was found."
        state.update_job(job_id, status=status, exit_code=exit_code, error=error)
    except Exception as exc:  # noqa: BLE001 - surface full local failure to the workbench.
        state.append_log(job_id, f"ERROR: {exc}")
        state.update_job(job_id, status="failed", error=str(exc))


def start_codex_job(state: WorkbenchState, payload: dict) -> dict:
    run_ref = str(payload.get("run_dir", "")).strip()
    if not run_ref:
        raise ValueError("run_dir is required")
    apply_settings(state, payload)

    approval_policy = str(payload.get("approval_policy", "on-request"))
    if approval_policy not in APPROVAL_POLICIES:
        raise ValueError(f"approval_policy must be one of {sorted(APPROVAL_POLICIES)}")

    run_dir = state.resolve_run_dir(run_ref)
    project_root = resolve_project_root(state, payload.get("project_root"))
    extra_instruction = str(payload.get("extra_instruction", "")).strip()
    job = state.create_job("codex")
    job_id = job["id"]
    thread = threading.Thread(
        target=_run_codex_job,
        args=(state, job_id, run_dir, project_root, approval_policy, extra_instruction),
        daemon=True,
    )
    thread.start()
    return job


def _run_codex_job(
    state: WorkbenchState,
    job_id: str,
    run_dir: Path,
    project_root: Path,
    approval_policy: str,
    extra_instruction: str,
) -> None:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"codex_exec_{job_id}.log"
    last_message_path = logs_dir / f"codex_last_message_{job_id}.md"
    sandbox = "workspace-write"
    codex_task_path = run_dir / "md" / "codex_task.md"
    if not codex_task_path.exists():
        state.update_job(job_id, status="failed", error=f"Missing {codex_task_path}")
        return
    materials = load_input_materials(run_dir)

    prompt = build_codex_prompt(
        run_dir=run_dir,
        project_root=project_root,
        codex_task_path=codex_task_path,
        materials=materials,
        extra_instruction=extra_instruction,
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
        state.update_job(
            job_id,
            run_dir=str(run_dir),
            run_url=f"/runs/{quote(run_dir.name)}/index.html",
            last_message_file=str(last_message_path),
        )
        state.append_log(job_id, "Codex 已启动，前台只显示最终决策摘要。")
        state.append_log(job_id, f"工作目录: {project_root}")
        state.append_log(job_id, f"执行模式: 任务范围内可修改，审批策略 {approval_policy}")
        state.append_log(job_id, f"完整原始日志: {log_path}")
        exit_code = run_command(
            state,
            job_id,
            command,
            project_root,
            log_path,
            input_text=prompt,
            stream_output_to_job=True,
        )
        status = "success" if exit_code == 0 else "failed"
        summary_path = _write_codex_decision_summary(run_dir, job_id, last_message_path, exit_code)
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


def start_test_job(state: WorkbenchState, payload: dict) -> dict:
    test_path = str(payload.get("test_path", "")).strip()
    if not test_path:
        raise ValueError("test_path is required")
    env = str(payload.get("env", "test"))
    if env not in ("test", "production", "all"):
        raise ValueError("env must be test, production, or all")
    apply_settings(state, payload)
    project_root = resolve_project_root(state, payload.get("project_root"))
    job = state.create_job("test")
    job_id = job["id"]
    thread = threading.Thread(
        target=_run_test_job,
        args=(state, job_id, project_root, test_path, env),
        daemon=True,
    )
    thread.start()
    return job


def _run_test_job(
    state: WorkbenchState,
    job_id: str,
    project_root: Path,
    test_path: str,
    env: str,
) -> None:
    log_path = state.output_dir / "_workbench_logs" / f"test_{job_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        test_project_root = resolve_test_project_root(project_root)
        normalized_test_path = normalize_test_path(test_project_root, test_path)
        runner_py = test_project_root / "runner.py"
        command = [state.python_executable, str(runner_py), "--env", env, normalized_test_path]
        report_url = allure_report_url_for_env(env)
        state.update_job(job_id, command=command, log_file=str(log_path), test_env=env)
        state.append_log(job_id, f"$ {' '.join(command)}")
        state.append_log(job_id, f"工作区目录: {project_root}")
        state.append_log(job_id, f"测试项目目录: {test_project_root}")
        state.append_log(job_id, f"测试路径: {normalized_test_path}")
        state.append_log(job_id, f"环境: {env}")
        state.append_log(job_id, f"完整日志: {log_path}")
        if env == "all":
            state.append_log(job_id, "ALL 模式会生成 /allure/allure_report_test/ 和 /allure/allure_report_production/ 两份报告。")
        exit_code = run_command(
            state,
            job_id,
            command,
            test_project_root,
            log_path,
            stream_output_to_job=True,
        )
        status = "success" if exit_code == 0 else "failed"
        state.update_job(
            job_id,
            status=status,
            exit_code=exit_code,
            allure_report_url=report_url,
        )
        passed = exit_code == 0
        state.append_log(job_id, "")
        state.append_log(job_id, "=" * 40)
        if env == "all" and passed:
            state.append_log(job_id, "ALL 模式执行完成，请分别查看 test / production 两份 Allure 报告。")
        else:
            state.append_log(job_id, f"测试{'全部通过' if passed else '存在失败，请查看 Allure 报告'}")
        state.append_log(job_id, f"Exit code: {exit_code}")
    except Exception as exc:  # noqa: BLE001 - show exact local failure.
        state.append_log(job_id, f"ERROR: {exc}")
        state.update_job(job_id, status="failed", error=str(exc))


def _write_codex_decision_summary(run_dir: Path, job_id: str, last_message_path: Path, exit_code: int) -> Path:
    logs_dir = run_dir / "logs"
    summary_path = logs_dir / f"codex_decision_{job_id}.md"
    if last_message_path.exists():
        text = _repair_mojibake(last_message_path.read_text(encoding="utf-8", errors="replace")).strip()
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


def build_codex_prompt(
    run_dir: Path,
    project_root: Path,
    codex_task_path: Path,
    materials: list[dict],
    extra_instruction: str,
) -> str:
    codex_task = _compact_codex_task_for_prompt(codex_task_path.read_text(encoding="utf-8", errors="replace"))
    edit_mode = (
        "用户已经在本地工作台批准 Codex 执行。你可以在本次交接任务范围内进行最小必要修改和验证；"
        "修改代码前仍必须遵守 AGENTS.md 和本提示中的确认规则。"
        "如果发现目标文件、选择器、测试数据、环境影响或验证命令超出交接任务，必须停止并说明需要新的确认。"
    )
    extra = f"\n\n用户补充指令：\n{extra_instruction}\n" if extra_instruction else ""
    materials_text = _build_codex_materials_text(materials)
    return f"""你现在由 auto-test-flow 本地工作台调用。

项目根目录：
{project_root}

交接产物目录：
{run_dir}

输入材料：
{materials_text}

执行模式：
{edit_mode}

必须遵循：
- 读取并遵守项目 AGENTS.md。若当前目录未看到 `AGENTS.md`，先检查当前目录父级；本地工作台会优先把 Codex 工作目录提升到包含 `AGENTS.md` 的工作区根目录。
- 优先调用或遵循 `karpathy-12-rules` skill；如果当前环境无法加载该 skill，则按其核心纪律执行：先读代码、少改、不要猜、不要过度设计、暴露假设、定义验证闭环。
- 接管阶段优先读取已生成的结构化产物：`json/test_cases.json`、`json/automation_request.json`、`json/execution_request.json`、`json/input_materials.json`、`md/test_cases.md`、`md/test_plan.md` 和当前 `md/codex_task.md`。这些产物已经承载原始材料解析结果。
- 不要默认回头解析原始 `.xlsx/.xls` 附件；只有结构化产物明显缺失、互相冲突，或用户明确要求核对原始附件时，才说明原因并请求读取原始附件。
- 如果读取或解析 `.xlsx/.xls` 的命令被 sandbox/policy 拒绝，必须停止这条路径，改读结构化产物；不要反复更换 Python、tar、PowerShell、压缩包解析等命令继续尝试。
- UI 选择器、页面对象、点击、读取、断言、流程修改前，必须有 CDP/F12/真实 DOM 证据；没有证据时不要猜选择器或加兜底逻辑。
- 如果缺少 DOM 证据，必须先在项目和本 skill 中查找并优先复用现有 CDP/元素证据采集能力，例如 `auto-test/core/utils/electron_cdp.py`、`auto-test/core/base/base_electron_page.py`、`auto-test/conftest.py` 的 CDP fixture，以及 `autotest-flow/skills/auto-test-flow/scripts/element_evidence.py`。只有确认本地没有可用采集路径，才请求用户提供 F12 DOM。
- 需要 DOM 证据时，不要要求用户手工抄 DOM；应先输出“证据采集计划”，说明准备打开的页面、会读取的元素、是否会登录、是否会保存配置、建议命令和环境影响，并等待用户授权。
- 用户授权采集后，优先用 CDP/Playwright 读取真实 DOM、outerHTML、checked/value/class/selected/disabled 等状态变化，再基于证据提出 selector/page object/testcase 修改计划。
- 修改必须保持最小可验证范围，不能为了通过测试削弱断言或隐藏产品问题。
- 如需执行管理员权限、网络、GUI、会修改店铺配置或测试环境数据的命令，先说明影响并等待用户确认。
- 分析范围优先限定在交接产物、AGENTS.md、相关测试用例、page object、selector 和最窄必要的公共辅助代码；如果扩大范围，必须说明原因。
- 结束时只输出给用户做决策的摘要，不要复述代码库扫描过程、完整命令输出或中间推理。
- 最终消息必须包含：结论、是否需要用户确认、准备修改/已修改文件、修改原因、已执行/未执行命令、剩余风险。
{extra}
下面是由 `md/codex_task.md` 压缩生成的精简交接摘要；如摘要不足，再按路径读取完整产物：

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
    lines.extend([
        "## 执行重点",
        "",
        "- 优先读取上面的 `json/` 与 `md/` 结构化产物，不要默认重新解析原始 `.xlsx/.xls`。",
        "- 如果结构化产物缺失或互相冲突，先说明缺口，再请求是否读取原始附件。",
        "- 不要重复展开 Karpathy、修改确认、CDP/F12 Gate；这些规则已经由工作台模板和 AGENTS.md 注入。",
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


def _existing_run_dirs(output_dir: Path) -> set[Path]:
    if not output_dir.exists():
        return set()
    return {path.resolve() for path in output_dir.iterdir() if path.is_dir() and (path / "index.html").exists()}


def _find_new_run_dir(output_dir: Path, before: set[Path]) -> Path | None:
    candidates = []
    for path in output_dir.iterdir():
        if not path.is_dir() or not (path / "index.html").exists():
            continue
        resolved = path.resolve()
        if resolved not in before:
            candidates.append(path)
    if not candidates:
        candidates = [path for path in output_dir.iterdir() if path.is_dir() and (path / "index.html").exists()]
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None


class WorkbenchHandler(BaseHTTPRequestHandler):
    server: "WorkbenchHTTPServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name.
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/":
                self._send_html(build_index_html(self.server.state))
                return
            if parsed.path.startswith("/assets/"):
                self._serve_asset_file(parsed.path)
                return
            if parsed.path == "/api/runs":
                self._send_json({"runs": self.server.state.list_runs()})
                return
            if parsed.path.startswith("/api/jobs/"):
                job_id = parsed.path.rsplit("/", 1)[-1]
                job = self.server.state.get_job(job_id)
                if not job:
                    self._send_json({"error": "job not found"}, HTTPStatus.NOT_FOUND)
                    return
                self._send_json({"job": job})
                return
            if parsed.path.startswith("/runs/"):
                self._serve_run_file(parsed.path)
                return
            if parsed.path.startswith("/allure/"):
                self._serve_allure_file(parsed.path)
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001 - local diagnostic API.
            self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def do_POST(self) -> None:  # noqa: N802 - stdlib API name.
        try:
            payload = self._read_json()
            if self.path == "/api/generate":
                job = start_generation_job(self.server.state, payload)
                self._send_json({"job": job})
                return
            if self.path == "/api/codex":
                job = start_codex_job(self.server.state, payload)
                self._send_json({"job": job})
                return
            if self.path == "/api/run-tests":
                job = start_test_job(self.server.state, payload)
                self._send_json({"job": job})
                return
            if self.path == "/api/settings":
                settings = apply_settings(self.server.state, payload)
                self._send_json(settings)
                return
            if self.path == "/api/select-directory":
                selected = select_directory(
                    str(payload.get("initial_dir", "")).strip(),
                    str(payload.get("title", "选择目录")).strip(),
                )
                self._send_json({"path": selected})
                return
            if self.path == "/api/select-file":
                selected = select_file(
                    str(payload.get("initial_dir", "")).strip(),
                    str(payload.get("title", "选择文件")).strip(),
                )
                self._send_json({"path": selected})
                return
            if self.path == "/api/runs/delete":
                run_ref = str(payload.get("run_dir") or payload.get("path") or payload.get("name") or "").strip()
                if not run_ref:
                    raise ValueError("run_dir is required")
                self.server.state.delete_run(run_ref)
                self._send_json({"ok": True})
                return
            if self.path.startswith("/api/jobs/") and self.path.endswith("/input"):
                job_id = self.path.split("/")[-2]
                text = str(payload.get("input", ""))
                if not text.strip():
                    raise ValueError("input is required")
                job = self.server.state.send_input(job_id, text)
                self._send_json({"job": job})
                return
            self._send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
        except Exception as exc:  # noqa: BLE001 - return useful workbench error.
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - stdlib signature.
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), format % args))

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _serve_asset_file(self, request_path: str) -> None:
        rel = request_path[len("/assets/") :]
        rel = posixpath.normpath(unquote(rel)).lstrip("/")
        if not rel or rel.startswith("../"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        target = (_asset_dir() / rel).resolve()
        asset_root = _asset_dir().resolve()
        if asset_root not in target.parents and target != asset_root:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if _is_text_mime(mime_type) else mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_run_file(self, request_path: str) -> None:
        rel = request_path[len("/runs/") :]
        rel = posixpath.normpath(unquote(rel)).lstrip("/")
        if not rel or rel.startswith("../"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        parts = rel.split("/")
        run_name = parts[0]
        subpath = Path(*parts[1:]) if len(parts) > 1 else Path("index.html")
        run_dir = self.server.state.resolve_run_dir(run_name)
        target = (run_dir / subpath).resolve()
        if run_dir.resolve() not in target.parents and target != run_dir.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if _is_text_mime(mime_type) else mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_allure_file(self, request_path: str) -> None:
        rel = request_path[len("/allure/") :]
        rel = posixpath.normpath(unquote(rel)).lstrip("/")
        if not rel or rel.startswith("../"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            test_project_root = resolve_test_project_root(self.server.state.project_root)
        except ValueError as exc:
            self.send_error(HTTPStatus.NOT_FOUND, str(exc))
            return
        report_root = (test_project_root / "testreport").resolve()
        target = (report_root / rel).resolve()
        if report_root not in target.parents and target != report_root:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        mime_type = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{mime_type}; charset=utf-8" if _is_text_mime(mime_type) else mime_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_html(self, content: str) -> None:
        data = content.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class WorkbenchHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, handler_class, state: WorkbenchState):
        super().__init__(server_address, handler_class)
        self.state = state


def _is_text_mime(mime_type: str) -> bool:
    return mime_type.startswith("text/") or mime_type in {
        "application/json",
        "application/javascript",
        "application/xml",
    }


def _asset_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"


def _read_asset_text(filename: str) -> str:
    return (_asset_dir() / filename).read_text(encoding="utf-8")


def build_index_html(state: WorkbenchState) -> str:
    """Render the workbench shell from assets/workbench.html."""
    content = _read_asset_text("workbench.html")
    replacements = {
        "{{PORT}}": html.escape(str(state.port)),
        "{{PROJECT_ROOT}}": html.escape(str(state.project_root)),
        "{{OUTPUT_DIR}}": html.escape(str(state.output_dir)),
        "{{ORCHESTRATOR_PATH}}": html.escape(str(state.orchestrator_path)),
    }
    for token, value in replacements.items():
        content = content.replace(token, value)
    return content


def parse_args() -> argparse.Namespace:
    default_orchestrator = Path(__file__).resolve().parent / "orchestrator.py"
    default_codex = shutil.which("codex.cmd") or shutil.which("codex") or "codex.cmd"
    parser = argparse.ArgumentParser(
        description="自动化测试平台本地工作台",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--host", default="127.0.0.1", help="Local bind host")
    parser.add_argument("--port", type=int, default=8765, help="Local bind port")
    parser.add_argument("--project-root", default=str(Path.cwd()), help="Project root passed to codex exec")
    parser.add_argument("--output-dir", default=str(Path.cwd() / "output"), help="Pipeline output directory")
    parser.add_argument("--orchestrator", default=str(default_orchestrator), help="Path to orchestrator.py")
    parser.add_argument("--python", default=sys.executable, help="Python executable used for orchestrator.py")
    parser.add_argument("--codex-command", default=default_codex, help="codex executable, usually codex.cmd on Windows")
    parser.add_argument("--open-browser", action="store_true", help="Open the local workbench URL in the default browser")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    state = WorkbenchState(
        host=args.host,
        port=args.port,
        orchestrator_path=Path(args.orchestrator),
        output_dir=Path(args.output_dir),
        project_root=Path(args.project_root),
        python_executable=args.python,
        codex_command=args.codex_command,
        model=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
    )
    server = WorkbenchHTTPServer((state.host, state.port), WorkbenchHandler, state)
    url = f"http://{state.host}:{state.port}/"
    print(f"自动化测试平台: {url}")
    print(f"Project root: {state.project_root}")
    print(f"Output dir:   {state.output_dir}")
    print("Press Ctrl+C to stop.")
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping workbench.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
