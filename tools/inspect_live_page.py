from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


output_dir = Path.cwd() / "logs"
output_dir.mkdir(exist_ok=True)

with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
    pages = [page for context in browser.contexts for page in context.pages]
    page = next((page for page in pages if "studentstudy" in page.url), None)
    if page is None:
        raise RuntimeError("调试 Edge 中未找到学生学习页面。")

    quiz_frame = next((frame for frame in page.frames if frame.locator(".questionLi").count()), None)
    if quiz_frame is None:
        raise RuntimeError("当前页面未找到题目，请打开一个章节测验后再运行。")

    first = quiz_frame.locator(".questionLi").first
    html = first.evaluate("el => el.outerHTML")
    text = first.inner_text()
    (output_dir / "live-question.html").write_text(html, encoding="utf-8")
    (output_dir / "live-question.json").write_text(
        json.dumps({"text": text, "html_length": len(html)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"已保存题目结构，HTML 长度：{len(html)}")
    print(text[:500])
