from __future__ import annotations

import copy
import threading
import unittest
import itertools
from dataclasses import replace
from unittest.mock import patch

from autoquiz.course import AssessmentReport, CourseNode, CourseWorkflow, NodeKind
from autoquiz.parser import parse_quiz
from autoquiz.models import Option
from tests.test_core import make_raw


class CourseStub:
    def __init__(self, reports=None, nodes=None):
        self.nodes = nodes or [CourseNode("exam", "本地合成考试", NodeKind.ASSESSMENT, "pending")]
        self.index = 0
        self.quiz = parse_quiz(make_raw())
        self.reports = reports or [AssessmentReport(100)]
        self.attempt = 0
        self.opens = self.fills = self.submits = self.reviews = self.restarts = self.moves = self.scrolls = 0
        self.selected = {}
        self.complete_after_scroll = True

    def current_node(self):
        return self.nodes[self.index]

    def advance_reading(self):
        self.scrolls += 1
        if self.complete_after_scroll:
            self.nodes[self.index] = replace(self.current_node(), status="completed")
        return False

    def open_assessment(self):
        self.opens += 1

    def extract_assessment(self):
        return copy.deepcopy(self.quiz)

    def fill_answers(self, answers):
        self.fills += 1
        self.selected = dict(answers)

    def selected_answers(self):
        return self.selected

    def submit_assessment(self, _answers):
        self.submits += 1
        return replace(self.reports[min(self.attempt, len(self.reports) - 1)], reviewed=False)

    def review_assessment(self):
        self.reviews += 1
        return self.reports[min(self.attempt, len(self.reports) - 1)]

    def restart_assessment(self):
        self.restarts += 1
        self.attempt += 1

    def next_node(self):
        self.moves += 1
        if self.index + 1 == len(self.nodes):
            return False
        self.index += 1
        return True


class SolverStub:
    def __init__(self):
        self.quizzes = []

    def solve(self, quiz):
        self.quizzes.append(quiz)
        result={}
        for q in quiz.questions:
            answer='AC' if q.kind.value=='多选题' else 'A'
            if answer in q.excluded_answers:
                size=q.required_choices or 1
                answer=next((''.join(c) for c in itertools.combinations([o.letter for o in q.options],size)
                             if ''.join(c) not in q.excluded_answers),'')
            result[q.number]=answer
        return result


class CourseTests(unittest.TestCase):
    def test_explicit_multiple_choice_count_must_be_satisfied(self):
        from autoquiz.answers import validate_answers
        browser=CourseStub()
        browser.quiz.questions[1].required_choices=2
        answers,issues=validate_answers(browser.quiz,{1:'A',2:'A',3:'B'})
        self.assertTrue(issues)
        self.assertNotIn(2,answers)

    def test_reading_requires_actual_completion_then_exam_is_solved(self):
        browser = CourseStub(nodes=[
            CourseNode("read", "阅读", NodeKind.READING, "pending"),
            CourseNode("done", "已完成", NodeKind.UNSUPPORTED, "completed"),
            CourseNode("exam", "考试", NodeKind.ASSESSMENT, "pending"),
        ])
        solver = SolverStub()
        workflow = CourseWorkflow(browser, solver, dry_run=False)
        stats = workflow.run()
        self.assertEqual(workflow.last_error, "")
        self.assertEqual(stats.already_completed, 2)
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(browser.scrolls, 1)
        self.assertEqual(browser.submits, 1)

    def test_wrong_review_is_used_by_content_after_question_and_option_shuffle(self):
        browser = CourseStub([
            AssessmentReport(80, (1,), {1: "请重新区分两项概念"}, True),
            AssessmentReport(100),
        ])
        original = browser.restart_assessment

        def restart_with_shuffle():
            original()
            first, second, third = browser.quiz.questions
            browser.quiz.questions = [replace(third, number=1), second, replace(first, number=3, options=list(reversed(first.options)))]

        solver = SolverStub()
        with patch.object(browser, "restart_assessment", side_effect=restart_with_shuffle):
            workflow = CourseWorkflow(browser, solver, dry_run=False)
            stats = workflow.run()
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(len(solver.quizzes), 2)
        self.assertEqual(browser.fills, 2)
        self.assertFalse(solver.quizzes[1].questions[0].retry_context)
        self.assertIn("请重新区分", solver.quizzes[1].questions[2].retry_context)
        self.assertIn("上轮所选内容", solver.quizzes[1].questions[2].retry_context)
        self.assertFalse(browser.quiz.questions[2].retry_context)

    def test_three_retries_stop_on_current_exam_after_four_attempts(self):
        browser = CourseStub([AssessmentReport(80, (1,), {1: "题目解析"}, True)])
        browser.quiz.questions[0].options.extend([Option('C','丙'),Option('D','丁'),Option('E','戊')])
        workflow = CourseWorkflow(browser, SolverStub(), dry_run=False, max_retries=3)
        stats = workflow.run()
        self.assertEqual(browser.submits, 4)
        self.assertEqual(browser.restarts, 3)
        self.assertEqual(browser.moves, 0)
        self.assertEqual(stats.errors, 1)
        self.assertEqual(stats.submitted, 0)
        self.assertIn("最多 3 次重做", workflow.last_error)

    def test_unknown_or_invalid_score_never_retries_or_navigates(self):
        for score in [None, float("nan"), float("inf"), -1, 101, True, "100"]:
            with self.subTest(score=score):
                browser = CourseStub([AssessmentReport(score)])
                workflow = CourseWorkflow(browser, SolverStub(), dry_run=False)
                self.assertEqual(workflow.run().errors, 1)
                self.assertEqual(browser.restarts, 0)
                self.assertEqual(browser.moves, 0)

    def test_missing_inconsistent_or_out_of_range_review_never_restarts(self):
        for report in [
            AssessmentReport(80),
            AssessmentReport(80, (4,), {4: "解析"}, True),
            AssessmentReport(80, (1, 1), {1: "解析"}, True),
        ]:
            with self.subTest(report=report):
                browser = CourseStub([report])
                workflow = CourseWorkflow(browser, SolverStub(), dry_run=False)
                self.assertEqual(workflow.run().errors, 1)
                self.assertEqual(browser.restarts, 0)
                self.assertEqual(browser.moves, 0)

    def test_wrong_marker_without_explanation_is_enough_to_retry_with_exclusion(self):
        browser=CourseStub([AssessmentReport(80,(1,),{},True),AssessmentReport(100)])
        solver=SolverStub();flow=CourseWorkflow(browser,solver,dry_run=False)
        self.assertEqual(flow.run().submitted,1)
        self.assertEqual(browser.restarts,1)
        self.assertEqual(solver.quizzes[1].questions[0].excluded_answers,('A',))

    def test_dry_run_leaves_answers_on_current_page_without_submission_or_navigation(self):
        browser = CourseStub()
        stats = CourseWorkflow(browser, SolverStub()).run()
        self.assertEqual(stats.tested, 1)
        self.assertEqual(browser.fills, 1)
        self.assertEqual(browser.submits, 0)
        self.assertEqual(browser.moves, 0)

    def test_dry_run_does_not_scroll_reading_that_may_save_progress(self):
        browser = CourseStub(nodes=[CourseNode("read", "阅读", NodeKind.READING, "pending")])
        CourseWorkflow(browser, SolverStub()).run()
        self.assertEqual(browser.scrolls, 0)
        self.assertEqual(browser.moves, 0)

    def test_stopping_after_recovered_review_never_resets_or_calls_ai(self):
        browser=CourseStub()
        solver=SolverStub()
        event=threading.Event()
        def recover():
            event.set()
            return browser.quiz,{1:'A',2:'AC',3:'A'},AssessmentReport(80,(1,),{},True)
        browser.resume_review_assessment=recover
        workflow=CourseWorkflow(browser,solver,dry_run=False,stop_event=event)
        self.assertEqual(workflow.run().errors,0)
        self.assertEqual(browser.restarts,0)
        self.assertEqual(browser.submits,0)
        self.assertEqual(solver.quizzes,[])

    def test_bottom_of_reading_without_completed_mark_never_navigates(self):
        browser = CourseStub(nodes=[CourseNode("read", "阅读", NodeKind.READING, "pending")])
        browser.complete_after_scroll = False
        workflow = CourseWorkflow(browser, SolverStub(), dry_run=False)
        self.assertEqual(workflow.run().errors, 1)
        self.assertEqual(browser.moves, 0)
        self.assertIn("完成标记未更新", workflow.last_error)

    def test_unknown_or_unsupported_node_never_navigates(self):
        for node in [
            CourseNode("x", "未知状态", NodeKind.READING),
            CourseNode("x", "外部实验", NodeKind.UNSUPPORTED, "pending"),
        ]:
            browser = CourseStub(nodes=[node])
            workflow = CourseWorkflow(browser, SolverStub(), dry_run=False)
            self.assertEqual(workflow.run().errors, 1)
            self.assertEqual(browser.moves, 0)

    def test_incomplete_or_changed_quiz_never_fills(self):
        browser = CourseStub()
        solver = SolverStub()
        with patch.object(solver, "solve", return_value={1: "A"}):
            self.assertEqual(CourseWorkflow(browser, solver, dry_run=False).run().errors, 1)
        self.assertEqual(browser.fills, 0)
        with patch.object(solver, "solve", side_effect=lambda quiz: (browser.nodes.__setitem__(0, replace(browser.current_node(), key="changed")) or {1: "A", 2: "AC", 3: "A"})):
            self.assertEqual(CourseWorkflow(browser, solver, dry_run=False).run().errors, 1)
        self.assertEqual(browser.fills, 0)
        self.assertEqual(browser.submits, 0)

    def test_stop_after_ai_fill_review_or_restart_prevents_next_write(self):
        for operation in ["solve", "fill_answers", "review_assessment", "restart_assessment"]:
            with self.subTest(operation=operation):
                browser = CourseStub([AssessmentReport(80, (1,), {1: "解析"}, True)])
                solver = SolverStub()
                event = threading.Event()
                target = solver if operation == "solve" else browser
                original = getattr(target, operation)

                def stop_after(*args):
                    value = original(*args)
                    event.set()
                    return value

                with patch.object(target, operation, side_effect=stop_after):
                    stats = CourseWorkflow(browser, solver, dry_run=False, stop_event=event).run()
                self.assertEqual(stats.errors, 0)
                self.assertEqual(browser.moves, 0)
                self.assertEqual(browser.submits, 0 if operation in {"solve", "fill_answers"} else 1)
                self.assertEqual(browser.restarts, 1 if operation == "restart_assessment" else 0)
                if operation == "solve":
                    self.assertEqual(browser.fills, 0)


if __name__ == "__main__":
    unittest.main()
