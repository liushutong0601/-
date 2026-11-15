from __future__ import annotations

import re
from typing import Any

from .models import Option, Question, QuestionType, Quiz


_TOTAL_RE = re.compile(r"题量\s*[:：]?\s*(\d+)")
_TYPE_COUNT_RE = re.compile(r"(单选题|多选题|判断题|填空题|材料题)\s*[（(]\s*共\s*(\d+)\s*题\s*[)）]")


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def parse_quiz(raw: dict[str, Any]) -> Quiz:
    """将浏览器提取的原始数据转换为测验，并保留所有 fail-closed 校验信息。"""
    body_text = _clean_text(raw.get("body_text"))
    total_match = _TOTAL_RE.search(body_text)
    declared_total = int(total_match.group(1)) if total_match else None
    type_counts = {
        match.group(1): int(match.group(2))
        for match in _TYPE_COUNT_RE.finditer(body_text)
    }

    questions: list[Question] = []
    for index, item in enumerate(raw.get("questions") or [], start=1):
        raw_type = _clean_text(item.get("raw_type") or item.get("kind"))
        kind = QuestionType.from_text(raw_type)
        try:
            number = int(item.get("number", index))
        except (TypeError, ValueError):
            number = index

        options: list[Option] = []
        seen_letters: set[str] = set()
        for option in item.get("options") or []:
            letter = _clean_text(option.get("letter")).upper().rstrip(".、")
            if not re.fullmatch(r"[A-Z]", letter) or letter in seen_letters:
                continue
            seen_letters.add(letter)
            options.append(
                Option(
                    letter=letter,
                    text=_clean_text(option.get("text")),
                    selected=bool(option.get("selected", False)),
                )
            )

        questions.append(
            Question(
                number=number,
                kind=kind,
                stem=_clean_text(item.get("stem")),
                options=options,
                element_id=str(item.get("element_id") or ""),
                has_images=bool(item.get("has_images", False)),
                raw_type=raw_type,
                screenshot_b64=str(item.get("screenshot_b64") or ""),
                screenshot_error=str(item.get("screenshot_error") or ""),
            )
        )

    return Quiz(
        declared_total=declared_total,
        type_counts=type_counts,
        questions=questions,
        body_text=body_text,
    )
