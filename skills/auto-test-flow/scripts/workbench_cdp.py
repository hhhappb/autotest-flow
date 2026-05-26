#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CDP preflight helpers for the auto-test-flow workbench."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import time
from pathlib import Path


DEFAULT_FEIKUA_APP_PATH = Path(r"C:\Users\admin1\AppData\Local\feikua\other\Application\Feikua_Browser.exe")
DEFAULT_ELECTRON_DEBUG_PORT = 9333
DEFAULT_STORE_DEBUG_PORT = 9222
CDP_PREFLIGHT_TIMEOUT_SECONDS = 20


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


def prepare_authorized_cdp_environment(
    state,
    job_id: str,
    project_root: Path,
    logs_dir: Path,
) -> str:
    """Reset Feikua browser into a predictable CDP-ready state before Codex runs."""
    if os.name != "nt":
        return "CDP 预检跳过：当前不是 Windows 环境，无法管理飞跨 Electron 进程。"

    test_project_root = resolve_test_project_root(project_root)
    app_path, electron_port, store_port = read_cdp_settings(test_project_root)
    lines = [
        "# 授权执行预检",
        "",
        f"- auto-test 项目目录: `{test_project_root}`",
        f"- 飞跨可执行文件: `{app_path}`",
        f"- Electron CDP 端口: `{electron_port}`",
        f"- 店铺浏览器 CDP 端口: `{store_port}`",
    ]
    state.append_log(job_id, "授权执行预检：准备飞跨 CDP 环境。")

    if not app_path.exists():
        message = f"飞跨可执行文件不存在: {app_path}"
        lines.append(f"- 结果: 失败，{message}")
        write_cdp_preflight_log(logs_dir, job_id, lines)
        raise FileNotFoundError(message)

    stopped = stop_feikua_browser_processes()
    lines.append(f"- 已关闭 Feikua_Browser 进程数: `{stopped}`")
    state.append_log(job_id, f"已关闭 Feikua_Browser 进程数: {stopped}")

    start_feikua_browser(app_path, electron_port)
    state.append_log(job_id, f"已启动飞跨 Electron: --rpa --remote-debugging-port={electron_port}")
    if not wait_for_port(electron_port, CDP_PREFLIGHT_TIMEOUT_SECONDS):
        message = f"等待 Electron CDP 端口 {electron_port} 监听超时"
        lines.append(f"- 结果: 失败，{message}")
        write_cdp_preflight_log(logs_dir, job_id, lines)
        raise RuntimeError(message)

    store_ready = is_port_open(store_port)
    lines.extend([
        f"- Electron CDP 状态: `{electron_port}` 已监听",
        f"- 店铺浏览器 CDP 状态: `{store_port}` {'已监听' if store_ready else '未监听，通常需要 Codex 打开店铺后再确认'}",
        "- 限制: 预检只负责准备主 Electron CDP，不保存店铺配置，不安装依赖。",
    ])
    write_cdp_preflight_log(logs_dir, job_id, lines)
    state.append_log(job_id, f"Electron CDP 端口 {electron_port} 已监听。")
    state.append_log(job_id, f"店铺浏览器 CDP 端口 {store_port}: {'已监听' if store_ready else '未监听，等待打开店铺后出现'}")
    return "\n".join(lines)


def read_cdp_settings(test_project_root: Path) -> tuple[Path, int, int]:
    config_path = test_project_root / "config" / "config.yaml"
    app_path = DEFAULT_FEIKUA_APP_PATH
    electron_port = DEFAULT_ELECTRON_DEBUG_PORT
    store_port = DEFAULT_STORE_DEBUG_PORT
    if config_path.exists():
        text = config_path.read_text(encoding="utf-8", errors="replace")
        app_match = re.search(r'electron_app_path:\s*"([^"]+)"', text)
        electron_match = re.search(r"electron_debug_port:\s*(\d+)", text)
        store_match = re.search(r"store_port:\s*(\d+)", text)
        if app_match:
            app_path = Path(app_match.group(1))
        if electron_match:
            electron_port = int(electron_match.group(1))
        if store_match:
            store_port = int(store_match.group(1))
    return app_path, electron_port, store_port


def stop_feikua_browser_processes() -> int:
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        "$items = @(Get-Process Feikua_Browser -ErrorAction SilentlyContinue); "
        "$count = $items.Count; "
        "if ($count -gt 0) { $items | Stop-Process -Force; Start-Sleep -Milliseconds 800 }; "
        "Write-Output $count",
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout or "Stop-Process failed").strip())
    output = (result.stdout or "0").strip().splitlines()
    return int(output[-1]) if output and output[-1].strip().isdigit() else 0


def start_feikua_browser(app_path: Path, electron_port: int) -> None:
    subprocess.Popen(
        [str(app_path), "--rpa", f"--remote-debugging-port={electron_port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_for_port(port: int, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if is_port_open(port):
            return True
        time.sleep(0.5)
    return False


def write_cdp_preflight_log(logs_dir: Path, job_id: str, lines: list[str]) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / f"codex_preflight_{job_id}.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


