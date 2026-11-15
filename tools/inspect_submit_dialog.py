from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import sync_playwright


with sync_playwright() as playwright:
    browser = playwright.chromium.connect_over_cdp("http://127.0.0.1:9222", timeout=8000)
    pages = [page for context in browser.contexts for page in context.pages]
    page = next((page for page in pages if "studentstudy" in page.url), None)
    if page is None:
        raise RuntimeError("未找到学生学习页面。")

    results = []
    for frame in page.frames:
        try:
            data = frame.evaluate(
                r"""
                () => {
                  const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                  };
                  const candidates = Array.from(document.querySelectorAll('body *'))
                    .filter(el => visible(el) && (el.innerText || '').includes('确认提交') && el.children.length < 30)
                    .sort((a, b) => a.querySelectorAll('*').length - b.querySelectorAll('*').length)
                    .slice(0, 8);
                  return candidates.map(el => ({
                    tag: el.tagName,
                    className: String(el.className || ''),
                    id: el.id || '',
                    childCount: el.querySelectorAll('*').length,
                    html: el.outerHTML.slice(0, 16000),
                    buttons: Array.from(el.querySelectorAll('a,button,[role="button"]')).map(button => ({
                      text: (button.innerText || '').trim(),
                      tag: button.tagName,
                      className: String(button.className || ''),
                      visible: visible(button)
                    }))
                  }));
                }
                """
            )
            if data:
                results.append({"url": frame.url, "candidates": data})
        except Exception:
            continue

output = Path.cwd() / "logs" / "submit-dialog-structure.json"
output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已保存 {len(results)} 个框架的确认弹窗结构：{output}")
for result in results:
    for candidate in result["candidates"]:
        print(candidate["tag"], candidate["className"], candidate["id"], candidate["buttons"])
