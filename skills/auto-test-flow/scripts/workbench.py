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
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
from xml.etree import ElementTree


FINAL_STATUSES = {"success", "failed"}
REVIEW_POLICIES = {"auto-review", "full-auto"}
APPROVAL_POLICIES = {"on-request", "never"}
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
    ) -> None:
        self.host = host
        self.port = port
        self.orchestrator_path = orchestrator_path.resolve()
        self.output_dir = output_dir.resolve()
        self.project_root = project_root.resolve()
        self.python_executable = python_executable
        self.codex_command = codex_command
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
            runs.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "url": f"/runs/{quote(path.name)}/index.html",
                    "modified_at": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
                    "review_decision": review.get("decision"),
                    "review_summary": review.get("summary"),
                    "review_counts": review.get("counts", {}),
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

    def update_settings(self, project_root: Path, output_dir: Path) -> None:
        self.project_root = project_root.resolve()
        self.output_dir = output_dir.resolve()
        self.output_dir.mkdir(parents=True, exist_ok=True)


def run_command(
    state: WorkbenchState,
    job_id: str,
    command: list[str],
    cwd: Path,
    log_path: Path,
    input_text: str | None = None,
    interactive: bool = False,
) -> int:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUNBUFFERED", "1")
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
                line = raw_line.rstrip("\n")
                log_file.write(raw_line)
                log_file.flush()
                state.append_log(job_id, line)

            if writer:
                writer.join(timeout=5)
            exit_code = process.wait()
            state.append_log(job_id, f"Exit code: {exit_code}")
            log_file.write(f"\nExit code: {exit_code}\n")
            return exit_code
        finally:
            state.detach_process(job_id)


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
            if material["summary"]:
                lines.append("  摘要：")
                for summary_line in material["summary"].splitlines():
                    lines.append("  " + summary_line)
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
            "",
            "```text",
            material.get("summary") or "",
            "```",
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
    return candidate


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
    state.update_settings(project_root, output_dir)
    return {
        "project_root": str(state.project_root),
        "output_dir": str(state.output_dir),
    }


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
        )
        run_dir = _find_new_run_dir(state.output_dir, before)
        if run_dir:
            write_input_materials(run_dir, materials, requirement)
            logs_dir = run_dir / "logs"
            logs_dir.mkdir(parents=True, exist_ok=True)
            final_log = logs_dir / "pipeline.log"
            shutil.copyfile(temp_log, final_log)
            state.update_job(
                job_id,
                run_dir=str(run_dir),
                run_url=f"/runs/{quote(run_dir.name)}/index.html",
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
    allow_edits = bool(payload.get("allow_edits"))
    extra_instruction = str(payload.get("extra_instruction", "")).strip()
    job = state.create_job("codex")
    job_id = job["id"]
    thread = threading.Thread(
        target=_run_codex_job,
        args=(state, job_id, run_dir, project_root, allow_edits, approval_policy, extra_instruction),
        daemon=True,
    )
    thread.start()
    return job


def _run_codex_job(
    state: WorkbenchState,
    job_id: str,
    run_dir: Path,
    project_root: Path,
    allow_edits: bool,
    approval_policy: str,
    extra_instruction: str,
) -> None:
    logs_dir = run_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"codex_exec_{job_id}.log"
    last_message_path = logs_dir / f"codex_last_message_{job_id}.md"
    sandbox = "workspace-write" if allow_edits else "read-only"
    codex_task_path = run_dir / "md" / "codex_task.md"
    if not codex_task_path.exists():
        state.update_job(job_id, status="failed", error=f"Missing {codex_task_path}")
        return
    materials = load_input_materials(run_dir)

    prompt = build_codex_prompt(
        run_dir=run_dir,
        project_root=project_root,
        codex_task_path=codex_task_path,
        allow_edits=allow_edits,
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
        "--sandbox",
        sandbox,
        "--ask-for-approval",
        approval_policy,
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
        exit_code = run_command(state, job_id, command, project_root, log_path, input_text=prompt)
        status = "success" if exit_code == 0 else "failed"
        state.update_job(job_id, status=status, exit_code=exit_code)
    except Exception as exc:  # noqa: BLE001 - show exact local failure.
        state.append_log(job_id, f"ERROR: {exc}")
        state.update_job(job_id, status="failed", error=str(exc))


def build_codex_prompt(
    run_dir: Path,
    project_root: Path,
    codex_task_path: Path,
    allow_edits: bool,
    materials: list[dict],
    extra_instruction: str,
) -> str:
    codex_task = codex_task_path.read_text(encoding="utf-8")
    edit_mode = (
        "用户已经在本地工作台勾选允许修改代码。你可以在本次交接任务范围内进行最小必要修改和验证；"
        "如果发现目标文件、选择器、测试数据、环境影响或验证命令超出交接任务，必须停止并说明需要新的确认。"
        if allow_edits
        else "用户尚未允许修改代码。只允许读取、分析、生成修改方案和风险清单，不得编辑文件或运行会改变环境状态的命令。"
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
- 读取并遵守项目 AGENTS.md。
- UI 选择器、页面对象、点击、读取、断言、流程修改前，必须有 CDP/F12/真实 DOM 证据；没有证据时不要猜选择器或加兜底逻辑。
- 修改必须保持最小可验证范围，不能为了通过测试削弱断言或隐藏产品问题。
- 如需执行管理员权限、网络、GUI、会修改店铺配置或测试环境数据的命令，先说明影响并等待用户确认。
- 结束时报告文件变更、命令结果、失败分类、剩余风险。
{extra}
下面是 `md/codex_task.md` 的完整内容：

```markdown
{codex_task}
```
"""


def _build_codex_materials_text(materials: list[dict]) -> str:
    if not materials:
        return "无附件材料。"
    lines = []
    for material in materials:
        lines.append(
            f"- {material.get('name')} ({material.get('kind')}): {material.get('path')}"
        )
        summary = str(material.get("summary") or "").strip()
        if summary:
            lines.append("  摘要：")
            for summary_line in summary.splitlines()[:120]:
                lines.append("  " + summary_line)
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

