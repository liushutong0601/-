"""只读检查专用 Edge 当前 NetAcad 页。不会开始、答题、滚动、重置或提交。

从项目根目录运行 python -m tools.inspect_netacad。输出仅保存到忽略的 logs 目录。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from autoquiz.netacad_probe import STRUCTURE_JS


def is_netacad_url(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "netacad.com" or host.endswith(".netacad.com")


def inspect(cdp_url: str) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url, timeout=8000)
        pages = [p for context in browser.contexts for p in context.pages if is_netacad_url(p.url)
                 and urlsplit(p.url).hostname != "auth.netacad.com" and "/launch" in urlsplit(p.url).path]
        if len(pages) != 1:
            raise RuntimeError("专用 Edge 中必须有且仅有一个 NetAcad 课程标签，避免检查错页。")
        page = pages[0]
        result = {"frames": [], "frame_errors": 0}
        for frame in page.frames:
            # 不检查外部助手、广告或其他平台的 frame。
            if not is_netacad_url(frame.url):
                continue
            try:
                parent = frame
                while parent.parent_frame is not None:
                    if not parent.frame_element().is_visible():
                        break
                    parent = parent.parent_frame
                else:
                    result["frames"].append(frame.evaluate(STRUCTURE_JS))
            except Exception:
                result["frame_errors"] += 1
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="NetAcad 页面只读结构诊断")
    parser.add_argument("--cdp-url", default="http://127.0.0.1:9222")
    args = parser.parse_args()
    try:
        result = inspect(args.cdp_url)
    except Exception:
        print("尚未读取到 NetAcad 页面。请在软件专用 Edge 中打开课程并登录，再重试。")
        return 1
    output = Path(__file__).resolve().parents[1] / "logs" / "netacad-structure.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已保存只读结构诊断：{output}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    markers = any(f["shadow_roots"] or f["visible_tree_items"] for f in result["frames"])
    return 0 if markers and not result["frame_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
