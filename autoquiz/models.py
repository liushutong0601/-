from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class QuestionType(str, Enum):
    SINGLE = "单选题"
    MULTIPLE = "多选题"
    JUDGE = "判断题"
    FILL = "填空题"
    MATERIAL = "材料题"
    UNSUPPORTED = "不支持的题型"

    @classmethod
    def from_text(cls, text: str) -> "QuestionType":
        for kind in (cls.SINGLE, cls.MULTIPLE, cls.JUDGE, cls.FILL, cls.MATERIAL):
            if kind.value in text:
                return kind
        return cls.UNSUPPORTED

    @property
    def supported(self) -> bool:
        return self in {self.SINGLE, self.MULTIPLE, self.JUDGE}


@dataclass(slots=True)
class Option:
    letter: str
    text: str
    selected: bool = False


@dataclass(slots=True)
class Question:
    number: int
    kind: QuestionType
    stem: str
    options: list[Option]
    element_id: str = ""
    has_images: bool = False
    raw_type: str = ""
    screenshot_b64: str = ""
    screenshot_error: str = ""

    @property
    def supported(self) -> bool:
        has_readable_content = bool(self.stem or self.screenshot_b64) and not self.screenshot_error
        return self.kind.supported and not self.has_images and has_readable_content and bool(self.options)

    @property
    def option_letters(self) -> set[str]:
        return {option.letter for option in self.options}


@dataclass(slots=True)
class Quiz:
    declared_total: int | None
    type_counts: dict[str, int]
    questions: list[Question]
    body_text: str = ""

    @property
    def total(self) -> int:
        return len(self.questions)

    def layout_issues(self) -> list[str]:
        issues: list[str] = []
        if self.declared_total is None or self.declared_total <= 0:
            issues.append("未读取到有效总题数")
        elif self.declared_total != self.total:
            issues.append(f"总题数 {self.declared_total} 与实际提取题数 {self.total} 不一致")

        if not self.type_counts:
            issues.append("未读取到各题型数量")
        elif sum(self.type_counts.values()) != self.declared_total:
            issues.append(
                f"各题型数量之和 {sum(self.type_counts.values())} 与总题数 {self.declared_total} 不一致"
            )

        numbers = [question.number for question in self.questions]
        if len(numbers) != len(set(numbers)):
            issues.append("题号存在重复")
        if self.declared_total and numbers and set(numbers) != set(range(1, self.declared_total + 1)):
            issues.append("题号不连续或存在缺题")
        return issues

    def structural_issues(self) -> list[str]:
        issues = self.layout_issues()
        for question in self.questions:
            if not question.supported:
                if question.has_images:
                    issues.append(f"第 {question.number} 题包含图片，当前版本跳过")
                elif not question.kind.supported:
                    issues.append(f"第 {question.number} 题为{question.kind.value}，当前版本跳过")
                else:
                    issues.append(f"第 {question.number} 题题干或选项提取不完整")
        return issues


@dataclass(slots=True)
class ChapterResult:
    chapter_key: str
    title: str = ""
    status: str = "skipped"
    detail: str = ""
    total: int = 0
    answered: int = 0
    submitted: bool = False


@dataclass(slots=True)
class RunStats:
    submitted: int = 0
    skipped: int = 0
    errors: int = 0
    tested: int = 0
    already_completed: int = 0
    results: list[ChapterResult] = field(default_factory=list)

    def add(self, result: ChapterResult) -> None:
        self.results.append(result)
        if result.status == "submitted":
            self.submitted += 1
        elif result.status == "tested":
            self.tested += 1
        elif result.status == "completed":
            self.already_completed += 1
        elif result.status == "error":
            self.errors += 1
        else:
            self.skipped += 1

    def summary(self) -> str:
        return (
            f"已提交 {self.submitted} 个章节，测试 {self.tested} 个章节，"
            f"跳过 {self.skipped} 个章节，异常 {self.errors} 个章节，"
            f"原本已完成 {self.already_completed} 个章节"
        )


def question_to_dict(question: Question) -> dict[str, Any]:
    return {
        "number": question.number,
        "type": question.kind.value,
        "stem": question.stem,
        "options": {option.letter: option.text for option in question.options},
    }
