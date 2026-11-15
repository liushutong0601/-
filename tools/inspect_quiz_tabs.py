from __future__ import annotations

import json
from pathlib import Path

from autoquiz.browser import EdgeBrowser


output = Path.cwd() / "logs" / "chapter-quiz-tabs.json"
records = []
with EdgeBrowser() as browser:
    page = browser.page_info()
    for frame in browser.page.frames:
        try:
            result = frame.evaluate(
                r"""
                () => {
                  const clean = value => (value || '').replace(/\s+/g, ' ').trim();
                  const visible = el => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return rect.width > 0 && rect.height > 0 && style.display !== 'none' && style.visibility !== 'hidden';
                  };
                  const candidates = Array.from(document.querySelectorAll('a, span, div, li, button'))
                    .filter(el => visible(el) && /章节(?:测验|检测)/.test(clean(el.innerText)))
                    .filter(el => clean(el.innerText).length <= 100)
                    .filter(el => !Array.from(el.children).some(child =>
                      visible(child) && clean(child.innerText) === clean(el.innerText)
                    ));
                  return {
                    url: location.href,
                    body: clean(document.body?.innerText).slice(0, 1200),
                    tabs: candidates.slice(0, 30).map(el => ({
                      tag: el.tagName,
                      id: el.id,
                      className: String(el.className || ''),
                      text: clean(el.innerText),
                      onclick: el.getAttribute('onclick') || '',
                      html: el.outerHTML.slice(0, 5000),
                      parentHtml: el.parentElement ? el.parentElement.outerHTML.slice(0, 8000) : ''
                    }))
                  };
                }
                """
            )
            if result.get("tabs"):
                records.append(result)
        except Exception as exc:
            records.append({"url": frame.url, "error": str(exc)})

output.write_text(json.dumps({"page": page, "frames": records}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已保存：{output}")
print(f"包含章节检测入口的框架数：{len(records)}")
for record in records:
    for tab in record.get("tabs", []):
        print(tab["tag"], tab["id"], tab["className"], tab["text"], tab["onclick"])
