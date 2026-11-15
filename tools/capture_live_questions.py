from __future__ import annotations

import base64
from pathlib import Path

from autoquiz.browser import EdgeBrowser
from autoquiz.parser import parse_quiz


with EdgeBrowser() as browser:
    if not browser.open_quiz():
        raise RuntimeError("当前小节没有章节测验。")
    quiz = parse_quiz(browser.extract_quiz())

output_dir = Path.cwd() / "logs"
output_dir.mkdir(exist_ok=True)
for question in quiz.questions:
    if question.screenshot_b64:
        path = output_dir / f"live-question-{question.number}.png"
        path.write_bytes(base64.b64decode(question.screenshot_b64))

print(f"动态题数：{quiz.declared_total}；实际提取：{quiz.total}")
print(f"截图成功：{sum(bool(question.screenshot_b64) for question in quiz.questions)}")
print(f"结构问题：{quiz.structural_issues()}")
