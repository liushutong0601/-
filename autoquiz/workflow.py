from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from .answers import answers_match, validate_answers
from .browser import BrowserError, BrowserStopped
from .kimi import SolverError
from .models import ChapterResult, Quiz, RunStats
from .parser import parse_quiz


class BrowserPort(Protocol):
    def open_quiz(self) -> bool: ...
    def is_quiz_completed(self) -> bool: ...
    def extract_quiz(self) -> dict: ...
    def current_chapter_key(self) -> str: ...
    def selected_answers(self) -> dict[int, str]: ...
    def fill_answers(self, answers: dict[int, str]) -> None: ...
    def submit(self, expected_answers: dict[int, str] | None = None) -> None: ...
    def current_directory_status(self) -> str: ...
    def next_unfinished_section(self) -> bool | None: ...
    def next_section(self) -> bool: ...


class SolverPort(Protocol):
    def solve(self, quiz: Quiz) -> dict[int, str]: ...


class _WorkflowStopped(Exception):
    """在浏览器或 AI 操作返回后结束流程，不将用户停止视为错误。"""


class QuizWorkflow:
    def __init__(
        self,
        browser: BrowserPort,
        solver: SolverPort,
        *,
        dry_run: bool = True,
        allow_fill: bool = True,
        logger: Callable[[str], None] | None = None,
        stop_event: threading.Event | None = None,
        max_chapters: int = 200,
    ) -> None:
        self.browser = browser
        self.solver = solver
        self.dry_run = dry_run
        self.allow_fill = allow_fill
        self.logger = logger or (lambda _message: None)
        self.stop_event = stop_event or threading.Event()
        self.max_chapters = max_chapters
        self.last_error = ""

    def analyze_current(self) -> tuple[Quiz | None, list[str]]:
        if not self.browser.open_quiz():
            return None, ["当前小节没有可识别的章节测验"]
        quiz = parse_quiz(self.browser.extract_quiz())
        return quiz, quiz.structural_issues()

    def _check_stopped(self) -> None:
        if self.stop_event.is_set():
            raise _WorkflowStopped

    def _move_next(self) -> bool:
        self._check_stopped()
        moved = self.browser.next_unfinished_section()
        self._check_stopped()
        if moved is not None:
            return moved
        # 旧版页面或目录结构暂时无法识别时，保留原有顺序导航作为兜底。
        moved = self.browser.next_section()
        self._check_stopped()
        return moved

    def run(self) -> RunStats:
        stats = RunStats()
        seen_keys: set[str] = set()

        try:
            for _ in range(self.max_chapters):
                self._check_stopped()

                chapter_key = self.browser.current_chapter_key()
                self._check_stopped()
                if chapter_key in seen_keys:
                    raise BrowserError("章节导航后仍停留在原章节，已停止以避免死循环。")
                seen_keys.add(chapter_key)
                self.logger(f"\n开始处理章节：{chapter_key}")

                directory_status = self.browser.current_directory_status()
                self._check_stopped()
                if directory_status == "completed":
                    stats.add(ChapterResult(chapter_key, status="completed", detail="目录显示已完成"))
                    self.logger("右侧目录显示绿勾，当前小节已完成，直接跳过。")
                elif not self.browser.open_quiz():
                    self._check_stopped()
                    stats.add(ChapterResult(chapter_key, status="skipped", detail="无章节测验"))
                    self.logger("当前小节无章节测验，继续寻找下一未完成小节。")
                elif self.browser.is_quiz_completed():
                    self._check_stopped()
                    stats.add(ChapterResult(chapter_key, status="completed", detail="此前已完成"))
                    self.logger("当前测验已经完成，进入下一节。")
                else:
                    self._check_stopped()
                    quiz = parse_quiz(self.browser.extract_quiz())
                    self._check_stopped()
                    issues = quiz.structural_issues()
                    self.logger(
                        f"读取到总题数 {quiz.declared_total}，实际提取 {quiz.total} 题，"
                        f"题型统计 {quiz.type_counts}"
                    )
                    if issues:
                        for issue in issues:
                            self.logger(f"禁止提交：{issue}")
                        stats.add(
                            ChapterResult(
                                chapter_key,
                                status="skipped",
                                detail="；".join(issues),
                                total=quiz.total,
                            )
                        )
                    else:
                        raw_answers = self.solver.solve(quiz)
                        self._check_stopped()
                        answers, answer_issues = validate_answers(quiz, raw_answers)
                        self.logger(f"获得有效答案 {len(answers)}／{quiz.total}。")
                        if answer_issues:
                            for issue in answer_issues:
                                self.logger(f"禁止提交：{issue}")
                            stats.add(
                                ChapterResult(
                                    chapter_key,
                                    status="skipped",
                                    detail="；".join(answer_issues),
                                    total=quiz.total,
                                    answered=len(answers),
                                )
                            )
                        else:
                            # 只有达到 100% 答案覆盖率后，才允许进入填写步骤。
                            if self.allow_fill:
                                self._check_stopped()
                                self.browser.fill_answers(answers)
                                self._check_stopped()
                                selected = self.browser.selected_answers()
                                self._check_stopped()
                                matched, fill_issues = answers_match(selected, answers)
                                if not matched:
                                    for issue in fill_issues:
                                        self.logger(f"禁止提交：{issue}")
                                    stats.add(
                                        ChapterResult(
                                            chapter_key,
                                            status="skipped",
                                            detail="；".join(fill_issues),
                                            total=quiz.total,
                                            answered=len(answers),
                                        )
                                    )
                                    if not self._move_next():
                                        self.logger("答题完成")
                                        self.logger(stats.summary())
                                        return stats
                                    continue

                            self._check_stopped()
                            if self.dry_run:
                                self.logger("测试模式：答案完整，未执行真实提交。")
                                stats.add(
                                    ChapterResult(
                                        chapter_key,
                                        status="tested",
                                        detail="答案完整，测试模式未提交",
                                        total=quiz.total,
                                        answered=len(answers),
                                    )
                                )
                            else:
                                if not self.allow_fill:
                                    raise BrowserError("真实运行必须启用自动填写和填写后校验。")
                                self._check_stopped()
                                self.browser.submit(answers)
                                self.logger("提交成功。")
                                stats.add(
                                    ChapterResult(
                                        chapter_key,
                                        status="submitted",
                                        detail="提交成功",
                                        total=quiz.total,
                                        answered=len(answers),
                                        submitted=True,
                                    )
                                )

                if not self._move_next():
                    self.logger("答题完成")
                    self.logger(stats.summary())
                    return stats

            raise BrowserError(f"已达到最大章节数 {self.max_chapters}，为防止死循环而停止。")
        except (_WorkflowStopped, BrowserStopped):
            self.logger("已按用户要求停止。")
            self.logger(stats.summary())
            return stats
        except (BrowserError, SolverError) as exc:
            self.last_error = str(exc)
            stats.errors += 1
            self.logger(f"运行已暂停：{exc}")
            self.logger(stats.summary())
            return stats
