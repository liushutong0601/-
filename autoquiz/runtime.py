from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


class BrowserSetupError(RuntimeError):
    pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / name


def app_data_dir() -> Path:
    if is_frozen():
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "AutoQuiz"
    else:
        base = Path.cwd()
    base.mkdir(parents=True, exist_ok=True)
    return base


def log_dir() -> Path:
    path = app_data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def edge_profile_dir() -> Path:
    path = app_data_dir() / ("edge-profile" if is_frozen() else ".edge-debug-profile")
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_edge_executable() -> Path:
    candidates = [
        Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise BrowserSetupError(
        "未找到 Microsoft Edge。请先安装 Edge，或确认它安装在系统默认位置。"
    )


def debug_endpoint_ready(cdp_url: str) -> bool:
    try:
        with urllib.request.urlopen(cdp_url.rstrip("/") + "/json/version", timeout=1.2) as response:
            return response.status == 200
    except Exception:
        return False


def ensure_debug_edge(cdp_url: str = "http://127.0.0.1:9222", *, start_url: str | None = None) -> str:
    """启动软件专用 Edge；已存在可用调试端点时直接复用。"""
    if debug_endpoint_ready(cdp_url):
        return "connected"

    edge = find_edge_executable()
    try:
        port = urlsplit(cdp_url).port or 9222
    except ValueError as exc:
        raise BrowserSetupError("Edge 调试地址格式不正确，请恢复为 http://127.0.0.1:9222。") from exc

    try:
        subprocess.Popen(
            [
                str(edge),
                f"--remote-debugging-port={port}",
                f"--user-data-dir={edge_profile_dir()}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-search-engine-choice-screen",
                start_url or "edge://newtab",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise BrowserSetupError(f"Edge 启动失败：{exc}") from exc

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if debug_endpoint_ready(cdp_url):
            return "launched"
        time.sleep(0.5)
    raise BrowserSetupError(
        "Edge 已启动，但调试连接在 15 秒内未就绪。请关闭软件专用 Edge 窗口后重试，"
        "并确认高级设置中的调试地址为 http://127.0.0.1:9222。"
    )
