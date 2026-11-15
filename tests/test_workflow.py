from __future__ import annotations

import copy
import threading
import unittest
from unittest.mock import patch

from autoquiz.workflow import QuizWorkflow
from tests.test_core import make_raw


class FakeBrowser:
    def __init__(self, chapters: list[dict], mismatch: bool = False) -> None:
        self.chapters = chapters
        self.index = 0
        self.mismatch = mismatch
        self.fill_calls = 0
        self.submit_calls = 0
        self.selected = {}
        self.fallback_navigation_calls = 0

    @property
    def current(self) -> dict:
        return self.chapters[self.index]

    def open_quiz(self) -> bool:
        return self.current.get("raw") is not None

    def is_quiz_completed(self) -> bool:
        return bool(self.current.get("completed", False))

    def extract_quiz(self) -> dict:
        return copy.deepcopy(self.current["raw"])

    def current_chapter_key(self) -> str:
        return f"chapter-{self.index + 1}"

    def current_directory_status(self) -> str:
        return self.current.get("directory_status", "unknown")

    def selected_answers(self) -> dict[int, str]:
        if self.mismatch:
            return {}
        return dict(self.selected)

    def fill_answers(self, answers: dict[int, str]) -> None:
        self.fill_calls += 1
        self.selected = dict(answers)

    def submit(self, expected_answers: dict[int, str] | None = None) -> None:
        self.submit_calls += 1
        self.current["submitted"] = True
        self.current["submit_expected"] = dict(expected_answers or {})
        if "directory_status" in self.current:
            self.current["directory_status"] = "completed"

    def next_unfinished_section(self) -> bool | None:
        if not any("directory_status" in chapter for chapter in self.chapters):
            return None
        for index in range(self.index + 1, len(self.chapters)):
            status = self.chapters[index].get("directory_status")
            if status == "completed":
                continue
            if status != "pending":
                return None
            self.index = index
            self.selected = {}
            return True
        return False

    def next_section(self) -> bool:
        self.fallback_navigation_calls += 1
        if self.index >= len(self.chapters) - 1:
            return False
        self.index += 1
        self.selected = {}
        return True


class FakeSolver:
    def __init__(self, results: list[dict[int, str]]) -> None:
        self.results = list(results)
        self.calls = 0

    def solve(self, _quiz) -> dict[int, str]:
        result = self.results[min(self.calls, len(self.results) - 1)]
        self.calls += 1
        return dict(result)


class WorkflowTests(unittest.TestCase):
    def assert_stopped_without_submission(self, stop_during: str, expected_fills: int) -> None:
        event = threading.Event()
        browser = FakeBrowser([{"raw": make_raw()}, {"raw": make_raw()}])
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}])
        target = solver if stop_during == "solve" else browser
        original = getattr(target, stop_during)

        def stop_after_operation(*args, **kwargs):
            result = original(*args, **kwargs)
            event.set()
            return result

        logs = []
        with patch.object(target, stop_during, side_effect=stop_after_operation):
            stats = QuizWorkflow(
                browser, solver, dry_run=False, stop_event=event, logger=logs.append
            ).run()
        self.assertEqual(browser.fill_calls, expected_fills)
        self.assertEqual(browser.submit_calls, 0)
        self.assertEqual(browser.index, 0)
        self.assertEqual(browser.fallback_navigation_calls, 0)
        self.assertEqual(stats.submitted, 0)
        self.assertEqual(stats.errors, 0)
        self.assertIn("已按用户要求停止。", logs)
        self.assertNotIn("答题完成", logs)

    def test_stop_during_solver_prevents_fill_submit_and_navigation(self) -> None:
        self.assert_stopped_without_submission("solve", 0)

    def test_stop_during_fill_prevents_submit_and_navigation(self) -> None:
        self.assert_stopped_without_submission("fill_answers", 1)

    def test_stop_during_selected_state_read_prevents_submit_and_navigation(self) -> None:
        self.assert_stopped_without_submission("selected_answers", 1)

    def test_stop_during_directory_read_prevents_fallback_and_completion(self) -> None:
        event = threading.Event()
        browser = FakeBrowser([{"raw": None}, {"raw": make_raw()}])

        def stop_during_directory_navigation():
            event.set()
            return None

        logs = []
        with patch.object(browser, "next_unfinished_section", side_effect=stop_during_directory_navigation):
            stats = QuizWorkflow(
                browser, FakeSolver([{}]), stop_event=event, logger=logs.append
            ).run()
        self.assertEqual(browser.index, 0)
        self.assertEqual(browser.fallback_navigation_calls, 0)
        self.assertEqual(stats.errors, 0)
        self.assertIn("已按用户要求停止。", logs)
        self.assertNotIn("答题完成", logs)

    def test_complete_answers_are_filled_verified_then_submitted(self) -> None:
        browser = FakeBrowser([{"raw": make_raw()}])
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}])
        logs = []
        stats = QuizWorkflow(browser, solver, dry_run=False, logger=logs.append).run()
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(browser.fill_calls, 1)
        self.assertEqual(browser.submit_calls, 1)
        self.assertIn("答题完成", logs)

    def test_incomplete_answers_never_fill_or_submit(self) -> None:
        browser = FakeBrowser([{"raw": make_raw()}])
        solver = FakeSolver([{1: "A", 2: "AC"}])
        stats = QuizWorkflow(browser, solver, dry_run=False).run()
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(browser.fill_calls, 0)
        self.assertEqual(browser.submit_calls, 0)

    def test_structural_error_never_calls_ai_or_submits(self) -> None:
        browser = FakeBrowser([{"raw": make_raw(4)}])
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}])
        stats = QuizWorkflow(browser, solver, dry_run=False).run()
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(solver.calls, 0)
        self.assertEqual(browser.submit_calls, 0)

    def test_dry_run_fills_but_never_submits(self) -> None:
        browser = FakeBrowser([{"raw": make_raw()}])
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}])
        stats = QuizWorkflow(browser, solver, dry_run=True).run()
        self.assertEqual(stats.tested, 1)
        self.assertEqual(browser.fill_calls, 1)
        self.assertEqual(browser.submit_calls, 0)

    def test_fill_mismatch_never_submits(self) -> None:
        browser = FakeBrowser([{"raw": make_raw()}], mismatch=True)
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}])
        stats = QuizWorkflow(browser, solver, dry_run=False).run()
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(browser.submit_calls, 0)

    def test_variable_chapter_sizes_and_final_summary(self) -> None:
        second = make_raw()
        second["questions"] = second["questions"][:1]
        second["body_text"] = "章节测验 题量：1 一、单选题（共1题）"
        browser = FakeBrowser([{"raw": make_raw()}, {"raw": second}])
        solver = FakeSolver([{1: "A", 2: "AC", 3: "B"}, {}])
        logs = []
        stats = QuizWorkflow(browser, solver, dry_run=False, logger=logs.append).run()
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(browser.submit_calls, 1)
        self.assertIn("答题完成", logs)

    def test_no_quiz_and_already_completed_are_not_submitted(self) -> None:
        browser = FakeBrowser([{"raw": None}, {"raw": make_raw(), "completed": True}])
        solver = FakeSolver([{}])
        stats = QuizWorkflow(browser, solver, dry_run=False).run()
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(stats.already_completed, 1)
        self.assertEqual(solver.calls, 0)
        self.assertEqual(browser.submit_calls, 0)

    def test_directory_runs_pending_quizzes_skips_green_and_finishes(self) -> None:
        answers = {1: "A", 2: "AC", 3: "B"}
        browser = FakeBrowser(
            [
                {"raw": make_raw(), "directory_status": "pending"},
                {"raw": make_raw(), "directory_status": "completed"},
                {"raw": None, "directory_status": "pending"},
                {"raw": make_raw(), "directory_status": "pending"},
            ]
        )
        solver = FakeSolver([answers, answers])
        logs = []
        stats = QuizWorkflow(browser, solver, dry_run=False, logger=logs.append).run()
        self.assertEqual(stats.submitted, 2)
        self.assertEqual(stats.skipped, 1)
        self.assertEqual(solver.calls, 2)
        self.assertEqual(browser.submit_calls, 2)
        self.assertEqual(browser.fallback_navigation_calls, 0)
        self.assertEqual(browser.index, 3)
        self.assertIn("答题完成", logs)

    def test_directory_green_current_is_skipped_and_all_green_ends(self) -> None:
        browser = FakeBrowser(
            [
                {"raw": make_raw(), "directory_status": "completed"},
                {"raw": None, "directory_status": "completed"},
            ]
        )
        solver = FakeSolver([{}])
        logs = []
        stats = QuizWorkflow(browser, solver, dry_run=False, logger=logs.append).run()
        self.assertEqual(stats.already_completed, 1)
        self.assertEqual(stats.submitted, 0)
        self.assertEqual(solver.calls, 0)
        self.assertEqual(browser.fill_calls, 0)
        self.assertIn("右侧目录显示绿勾，当前小节已完成，直接跳过。", logs)
        self.assertIn("答题完成", logs)


if __name__ == "__main__":
    unittest.main()
