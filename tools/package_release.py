from __future__ import annotations

import argparse
import shutil
import zipfile
from pathlib import Path

from autoquiz import __version__


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
APP_DIR = DIST / "自动搜题"
ARCHIVE = DIST / f"自动搜题-Windows-v{__version__}.zip"


def validate_release_tree(app_dir: Path = APP_DIR) -> None:
    required = [
        app_dir / "自动搜题.exe",
        app_dir / "README.md",
        app_dir / "_internal" / "README.md",
        app_dir / "docs" / "项目现状与团队分工.md",
        app_dir / "docs" / "Codex规则模式修复记录.md",
        app_dir / "_internal" / "playwright" / "driver" / "node.exe",
        app_dir / "_internal" / "playwright" / "driver" / "package" / "cli.js",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RuntimeError("发布目录缺少必要文件：" + "；".join(missing))

    forbidden_parts = {".edge-debug-profile", "edge-profile", "logs", ".env", "reference_screenshots"}
    for path in app_dir.rglob("*"):
        relative = path.relative_to(app_dir)
        if any(part in forbidden_parts for part in relative.parts):
            raise RuntimeError(f"发布目录含用户数据：{relative}")
        if path.is_file() and (path.suffix.lower() == ".log" or path.name.startswith("run-")):
            raise RuntimeError(f"发布目录含运行记录：{relative}")


def main(app_dir: Path = APP_DIR) -> None:
    app_dir = app_dir.resolve()
    if not app_dir.is_relative_to(DIST.resolve()) or app_dir.name != "自动搜题":
        raise RuntimeError("发布目录必须是 dist 下的“自动搜题”目录。")
    if not (app_dir / "自动搜题.exe").is_file():
        raise RuntimeError("未找到打包程序，请先运行 PyInstaller。")
    shutil.copy2(ROOT / "README.md", app_dir / "README.md")
    shutil.copy2(ROOT / "README.md", app_dir / "_internal" / "README.md")
    shutil.copytree(ROOT / "docs", app_dir / "docs", dirs_exist_ok=True)
    shutil.copytree(ROOT / "docs", app_dir / "_internal" / "docs", dirs_exist_ok=True)
    validate_release_tree(app_dir)

    if ARCHIVE.exists():
        ARCHIVE.unlink()
    files = sorted(path for path in app_dir.rglob("*") if path.is_file())
    with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, path.relative_to(app_dir.parent))

    with zipfile.ZipFile(ARCHIVE) as archive:
        broken = archive.testzip()
        if broken:
            raise RuntimeError(f"ZIP 校验失败：{broken}")
        names = archive.namelist()
        if "自动搜题/自动搜题.exe" not in names or "自动搜题/README.md" not in names:
            raise RuntimeError("ZIP 缺少软件入口或使用说明。")
        forbidden = (".edge-debug-profile", "edge-profile", "/logs/", ".env", "reference_screenshots")
        if any(any(item in name for item in forbidden) or name.endswith(".log") for name in names):
            raise RuntimeError("ZIP 含不应发布的用户数据。")

    size_mb = ARCHIVE.stat().st_size / (1024 * 1024)
    print(f"发布包：{ARCHIVE}")
    print(f"文件数：{len(files)}；压缩包大小：{size_mb:.1f} MB；ZIP 校验通过。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, default=APP_DIR, help="PyInstaller 生成的软件目录")
    main(parser.parse_args().app_dir)
