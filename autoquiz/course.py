"""多节点课程的流程层。页面适配器必须提供已核实的状态，不能用点击推断完成。"""
from __future__ import annotations

import math
import threading
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol

from .answers import answers_match, validate_answers
from .browser import BrowserError, BrowserStopped
from .kimi import SolverError
from .models import ChapterResult, Question, Quiz, RunStats
from .workflow import SolverPort
from .retry_memory import RetryMemory, question_fingerprint


class NodeKind(str, Enum):
    READING = "reading"
    ASSESSMENT = "assessment"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True, slots=True)
class CourseNode:
    key: str
    title: str
    kind: NodeKind
    status: str = "unknown"


@dataclass(frozen=True, slots=True)
class AssessmentReport:
    # None 与 0 分不同。成绩必须来自当前轮次可见的结果区域。
    score: float | None
    wrong_numbers: tuple[int, ...] = ()
    feedback: dict[int, str] = field(default_factory=dict)
    reviewed: bool = False
    correct_numbers: tuple[int, ...] = ()
    uncertain_wrong_numbers: tuple[int, ...] = ()
    correct_answers: dict[int, str] = field(default_factory=dict)
    ungraded_numbers: tuple[int, ...] = ()


class CoursePort(Protocol):
    def current_node(self) -> CourseNode: ...
    def advance_reading(self) -> bool: ...
    def open_assessment(self) -> None: ...
    def extract_assessment(self) -> Quiz: ...
    def fill_answers(self, answers: dict[int, str]) -> None: ...
    def selected_answers(self) -> dict[int, str]: ...
    def submit_assessment(self, answers: dict[int, str]) -> AssessmentReport: ...
    def review_assessment(self) -> AssessmentReport: ...
    def restart_assessment(self) -> None: ...
    def next_node(self) -> bool: ...


class SequentialCoursePort(CoursePort, Protocol):
    def assessment_steps(self) -> int | None: ...
    def extract_assessment_step(self, number: int) -> Quiz: ...
    def save_assessment_step(self, number: int, answers: dict[int, str]) -> None: ...
    def finish_assessment_steps(self) -> Quiz: ...


class CourseWorkflow:
    def __init__(
        self,
        browser: CoursePort,
        solver: SolverPort | None,
        *,
        dry_run: bool = True,
        max_retries: int = 3,
        max_nodes: int = 500,
        max_scrolls: int = 300,
        stop_event: threading.Event | None = None,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        if not 0 <= max_retries <= 5:
            raise ValueError("重做次数必须在 0～5 之间")
        self.browser = browser
        self.solver = solver
        self.dry_run = dry_run
        self.max_retries = max_retries
        self.max_nodes = max_nodes
        self.max_scrolls = max_scrolls
        self.stop_event = stop_event or threading.Event()
        self.logger = logger or (lambda _message: None)
        self.last_error = ""
        self.preview_ready = False

    def _check(self) -> None:
        if self.stop_event.is_set():
            raise BrowserStopped("已按用户要求停止。")

    def _call(self, name: str, *args):
        self._check()
        result = getattr(self.browser, name)(*args)
        self._check()
        return result

    def _same_node(self, expected: CourseNode) -> CourseNode:
        current = self._call("current_node")
        if current.key != expected.key or current.kind != expected.kind:
            raise BrowserError("课程节点已变化，已暂停，未执行后续填写或提交。")
        return current

    @staticmethod
    def _score(report: AssessmentReport) -> float:
        score = report.score
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
            raise BrowserError("未读取到本轮有效成绩，不能确认提交成功或开始重做。")
        if not 0 <= score <= 100:
            raise BrowserError("本轮成绩超出 0～100，已暂停。")
        return float(score)

    def _assessment(self, node: CourseNode) -> ChapterResult:
        existing=self._call('resume_review_assessment') if callable(getattr(self.browser,'resume_review_assessment',None)) else None
        if existing is None:
            self._call("open_assessment")
        memory = RetryMemory()
        for attempt in range(self.max_retries + 1):
            self._same_node(node)
            recovered=existing is not None and attempt==0
            if recovered:
                quiz,answers,report=existing
                score=self._score(report)
                if score==100:
                    self.preview_ready=self.dry_run
                    return ChapterResult(node.key,node.title,'completed','页面明确已有满分成绩')
                if quiz is None or quiz.structural_issues():
                    raise BrowserError('已有试卷无法完整复核，未重置。')
                answers, _ = validate_answers(quiz, answers)
                missing = {q.number for q in quiz.questions} - answers.keys()
                if missing - set(report.ungraded_numbers):
                    raise BrowserError('已有试卷无法完整复核，未重置。')
            else:
                quiz, answers = self._answer_round(node, memory)
            self._same_node(node)
            if self.dry_run:
                self.preview_ready = True
                message = "已读取已有判定，测试模式未重置" if recovered else "已填写并复核，未提交"
                return ChapterResult(node.key, node.title, "tested", message, quiz.total, len(answers))

            if not recovered:
                report = self._call("submit_assessment", answers)
            score = self._score(report)
            self.logger(f"本轮成绩 {score:g}%，第 {attempt + 1} 轮。")
            if score == 100:
                if report.wrong_numbers:
                    raise BrowserError("满分成绩与错题标记冲突，已暂停。")
                return ChapterResult(node.key, node.title, "submitted", "已核实本轮满分", quiz.total, len(answers), True)
            self._same_node(node)
            review = report if recovered and report.reviewed else self._call("review_assessment")
            if self._score(review) != score or not review.reviewed:
                raise BrowserError("未能核实本轮错题解析，已暂停，未开始重做。")
            wrong = review.wrong_numbers
            ungraded = review.ungraded_numbers
            numbers = {q.number for q in quiz.questions}
            if (not (wrong or ungraded) or len(set(wrong)) != len(wrong) or
                    len(set(ungraded)) != len(ungraded) or not (set(wrong) | set(ungraded)) <= numbers or
                    set(ungraded) & (set(wrong) | set(review.correct_numbers))):
                raise BrowserError("错题题号缺失、重复或越界，未开始重做。")
            memory.learn(quiz,answers,review)
            if wrong:
                self.logger("已定位错题：" + "、".join(map(str, wrong)))
            if ungraded:
                self.logger(f"有 {len(ungraded)} 道题未公开判定，重做时会重新解答尚未确认的题。")
            if review.correct_answers:
                self.logger(f"已记住平台解析提供的 {len(review.correct_answers)} 项正确答案。")
            if attempt == self.max_retries:
                raise BrowserError(f"已达到最多 {self.max_retries} 次重做，仍未满分，停在当前测验。")
            self._same_node(node)
            self._call("restart_assessment")
            # 新轮次重新提取全部题目，只重解尚未明确判对的题，再完整填写和复核。
        raise AssertionError("unreachable")

    def _solve_fill(self, node, quiz, memory):
        self._same_node(node)
        issues = quiz.structural_issues()
        if issues:
            raise BrowserError("测验结构未通过校验：" + "；".join(issues))
        prepared,fixed=memory.prepare(quiz)
        needed=[q for q in prepared.questions if q.number not in fixed]
        answers=dict(fixed)
        if fixed:
            self.logger(f'复用已明确判对的 {len(fixed)} 项答案，重新解答 {len(needed)} 项。')
        if needed:
            if self.solver is None:
                raise SolverError("尚未配置答题 API。")
            payload=Quiz(len(needed),dict(Counter(q.kind.value for q in needed)),
                         [replace(q,number=i) for i,q in enumerate(needed,1)])
            self._check()
            raw=self.solver.solve(payload)
            self._check()
            valid,issues=validate_answers(payload,raw)
            if issues:
                raise BrowserError("有效答案不足或重复选择已判错组合：" + "；".join(issues))
            answers.update({q.number:valid[i] for i,q in enumerate(needed,1)})
        answers, issues = validate_answers(prepared, answers)
        if issues:
            raise BrowserError("有效答案不足：" + "；".join(issues))
        self._same_node(node)
        self._call("fill_answers", answers)
        if not answers_match(self._call("selected_answers"), answers)[0]:
            raise BrowserError("填写复核未通过，已暂停。")
        self._same_node(node)
        return answers

    def _answer_round(self, node, memory):
        if callable(getattr(self.browser, 'resume_assessment', None)):
            resumed = self._call('resume_assessment')
            if resumed is not None:
                quiz, answers = resumed
                self._same_node(node)
                if quiz.structural_issues() or validate_answers(quiz, answers)[1]:
                    raise BrowserError("待提交试卷恢复复核失败，已禁止最终提交。")
                self.logger("已复核待提交试卷的全部现有答案，本轮不重新调用 AI 或填写。")
                return quiz, answers
        sequential = callable(getattr(self.browser, 'assessment_steps', None))
        total = self._call('assessment_steps') if sequential else None
        if total is None:
            quiz = self._call('extract_assessment')
            return quiz, self._solve_fill(node, quiz, memory)
        if isinstance(total, bool) or not isinstance(total, int) or not 1 <= total <= 500:
            raise BrowserError("逐题考试总题数无效，已暂停。")
        questions, answers = [], {}
        self.logger(f"本轮为逐题解锁考试，共 {total} 道网页题目。")
        restored = self._call('resume_assessment_steps') if callable(
            getattr(self.browser, 'resume_assessment_steps', None)) else []
        deferred = self._call('deferred_assessment_steps') if callable(
            getattr(self.browser, 'deferred_assessment_steps', None)) else 0
        if (isinstance(deferred,bool) or not isinstance(deferred,int) or deferred < 0 or
                len(restored)+deferred >= total or deferred and restored):
            raise BrowserError("中途恢复的题目范围无效，已暂停。")
        if restored:
            self.logger(f"已从页面缓存复核此前保存的 {len(restored)} 道题，未切回旧题，从第 {len(restored)+1} 题继续。")
        elif deferred:
            self.logger(f"此前 {deferred} 题已保存，直接从第 {deferred+1} 题继续；提交前统一复核整卷。")
        for step in range(deferred+1, total + 1):
            if step <= len(restored):
                quiz, current = restored[step-1]
                if quiz.structural_issues() or validate_answers(quiz, current)[1]:
                    raise BrowserError("已保存题目恢复校验失败，已暂停。")
            else:
                quiz = self._call('extract_assessment_step', step)
                current = self._solve_fill(node, quiz, memory)
                if self.dry_run:
                    self.logger(f"测试模式已预览第 {step}/{total} 题；未保存，不会解锁下一题。"
                                "要继续剩余题目，请在软件中选择“真实提交模式”并确认本次运行。")
                    return quiz, current
                self._call('save_assessment_step', step, current)
            for q in quiz.questions:
                number = len(questions) + 1
                questions.append(replace(q, number=number))
                answers[number] = current[q.number]
            if step > len(restored):
                self.logger(f"已核实保存第 {step}/{total} 题。")
        complete = self._call('finish_assessment_steps')
        expected = Quiz(len(questions), dict(Counter(q.kind.value for q in questions)), questions)
        prefix_items = complete.total-expected.total if deferred else 0
        tail = complete.questions[prefix_items:]
        if complete.structural_issues() or prefix_items < 0 or (not deferred and complete.total != expected.total) or [
                question_fingerprint(q) for q in tail] != [question_fingerprint(q) for q in questions]:
            raise BrowserError("逐题考试汇总与已保存题目不一致，已禁止最终提交。")
        if deferred:
            full_answers = self._call('completed_assessment_answers')
            full_answers, issues = validate_answers(complete,full_answers)
            if issues or any(full_answers.get(n+prefix_items) != answer for n,answer in answers.items()):
                raise BrowserError("逐题考试恢复后的答案汇总不一致，已禁止最终提交。")
            answers = full_answers
        self._same_node(node)
        return complete, answers

    def run(self) -> RunStats:
        stats = RunStats()
        visited: set[str] = set()
        try:
            for _ in range(self.max_nodes):
                node = self._call("current_node")
                if not node.key or node.key in visited:
                    raise BrowserError("课程节点标识缺失或导航重复，已暂停。")
                visited.add(node.key)
                self.logger(f"处理课程节点（{node.kind.value}）。")
                if node.status == "completed":
                    stats.add(ChapterResult(node.key, node.title, "completed", "目录明确显示已完成"))
                elif node.status != "pending":
                    raise BrowserError("节点完成状态未知，已暂停，未跳过当前节点。")
                elif node.kind == NodeKind.READING:
                    if self.dry_run:
                        self.preview_ready = True
                        self.logger("测试模式停在阅读节点；滚动可能写入平台进度。")
                        return stats
                    for _ in range(self.max_scrolls):
                        current = self._same_node(node)
                        if current.status == "completed":
                            stats.add(ChapterResult(node.key, node.title, "completed", "滚动后核实目录完成标记"))
                            break
                        if not self._call("advance_reading"):
                            # 已到底也需要重新读取标记，不能把到底推断成完成。
                            if self._same_node(node).status != "completed":
                                raise BrowserError("阅读已到底但完成标记未更新，停在当前节点。")
                            stats.add(ChapterResult(node.key, node.title, "completed", "核实目录完成标记"))
                            break
                    else:
                        raise BrowserError("阅读滚动达到上限，停在当前节点。")
                elif node.kind == NodeKind.ASSESSMENT:
                    stats.add(self._assessment(node))
                    if self.dry_run:
                        return stats  # 保留本轮填写现场，不导航到下一节点。
                else:
                    raise BrowserError("当前为未适配的节点或外部实验，需人工处理后继续。")
                if not self._call("next_node"):
                    self.logger("课程节点处理完成。")
                    return stats
            raise BrowserError("已达到课程节点数上限，已暂停。")
        except BrowserStopped:
            self.logger("已按用户要求停止。")
        except (BrowserError, SolverError) as exc:
            self.last_error = str(exc)
            stats.errors += 1
            self.logger(f"运行已暂停：{exc}")
        return stats
