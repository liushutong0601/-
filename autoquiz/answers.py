from __future__ import annotations

import re
from collections.abc import Mapping

from .models import Question, Quiz


_TRUE_VALUES = {"对", "正确", "是", "TRUE", "T", "√"}
_FALSE_VALUES = {"错", "错误", "否", "FALSE", "F", "×"}


def normalize_answer(question: Question, value: object) -> str | None:
    """把模型答案规范为选项字母；无法可靠映射时返回 None。"""
    text = str(value or "").strip().upper()
    if not text:
        return None

    if question.kind.value == "判断题" and text in _TRUE_VALUES | _FALSE_VALUES:
        wanted_true = text in _TRUE_VALUES
        keywords = ("对", "正确", "是") if wanted_true else ("错", "错误", "否")
        matches = [
            option.letter
            for option in question.options
            if any(keyword in option.text for keyword in keywords)
        ]
        if len(matches) != 1:
            return None
        text = matches[0]

    letters = re.findall(r"[A-Z]", text)
    if not letters:
        return None
    unique = set(letters)
    if len(unique) != len(letters) or not unique.issubset(question.option_letters):
        return None

    if question.kind.value == "单选题" or question.kind.value == "判断题":
        if len(unique) != 1:
            return None
    elif question.kind.value == "多选题" and not unique:
        return None
    if question.required_choices is not None and len(unique) != question.required_choices:
        return None

    normalized = "".join(option.letter for option in question.options if option.letter in unique)
    return None if normalized in question.excluded_answers else normalized


def validate_answers(quiz: Quiz, answers: Mapping[int, str]) -> tuple[dict[int, str], list[str]]:
    valid: dict[int, str] = {}
    issues: list[str] = []
    for question in quiz.questions:
        normalized = normalize_answer(question, answers.get(question.number))
        if normalized is None:
            issues.append(f"第 {question.number} 题没有有效答案")
        else:
            valid[question.number] = normalized
    return valid, issues


def answers_match(actual: Mapping[int, str], expected: Mapping[int, str]) -> tuple[bool, list[str]]:
    issues: list[str] = []
    for number, expected_answer in expected.items():
        if actual.get(number) != expected_answer:
            issues.append(
                f"第 {number} 题填写校验失败：期望 {expected_answer}，实际 {actual.get(number) or '未选择'}"
            )
    return not issues, issues
