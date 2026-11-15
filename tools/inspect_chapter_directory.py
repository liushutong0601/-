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
                  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
                  const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                  };
                  return Array.from(document.querySelectorAll('li,a,div'))
                    .filter(el => visible(el) && /^\d+\.\d+\s+/.test(clean(el.innerText)) && el.children.length < 15)
                    .slice(0, 40)
                    .map(el => ({
                      text: clean(el.innerText).slice(0, 120),
                      tag: el.tagName,
                      className: String(el.className || ''),
                      parentClass: String(el.parentElement?.className || ''),
                      html: el.outerHTML.slice(0, 5000)
                    }));
                }
                """
            )
            if data:
                results.append({"url": frame.url, "items": data})
        except Exception:
            continue

output = Path.cwd() / "logs" / "chapter-directory-structure.json"
output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已保存目录结构：{output}")
for result in results:
    print(f"框架：{result['url']}，项目数：{len(result['items'])}")
    for item in result["items"][:8]:
        print(item["text"], item["className"], item["parentClass"])
