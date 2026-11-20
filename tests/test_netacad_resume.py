from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import patch

from autoquiz.course import CourseWorkflow
from tests import test_netacad_sequential as sequential


class SequentialResumeTests(unittest.TestCase):
    """Resume against local synthetic pages without revisiting the saved prefix first."""

    setUpClass = classmethod(sequential.SequentialTests.setUpClass.__func__)
    tearDownClass = classmethod(sequential.SequentialTests.tearDownClass.__func__)
    setUp = sequential.SequentialTests.setUp
    tearDown = sequential.SequentialTests.tearDown
    load = sequential.SequentialTests.load
    prepare_saved_prefix = sequential.SequentialTests.prepare_saved_prefix

    def test_uncached_prefix_is_read_once_only_after_remaining_questions_are_saved(self):
        fresh = self.prepare_saved_prefix()
        # Some players expose the final navigation button without opening the form.
        self.frame.evaluate("""() => {
          const originalSave = saveStep;
          window.saveStep = () => {
            const openFinalPage = finalPage;
            if (question === expected) window.finalPage = () => {};
            try { originalSave(); }
            finally { window.finalPage = openFinalPage; }
          };
        }""")
        solver = sequential.Solver()
        before_choices = self.frame.evaluate("choiceClicks")
        old_navigation_before_solves = []
        prefix_reads = []
        original_read = fresh._read_assessment_step
        original_solve = solver.solve

        def read(number):
            if number <= 2:
                prefix_reads.append((number, len(solver.calls), self.frame.evaluate("stepSaves")))
            return original_read(number)

        with (
            patch.object(fresh, "_go_question", wraps=fresh._go_question) as navigate,
            patch.object(fresh, "_read_assessment_step", side_effect=read),
            patch.object(fresh, "save_assessment_step", wraps=fresh.save_assessment_step) as save,
        ):
            def solve(quiz):
                old_navigation_before_solves.append(
                    [call.args[0] for call in navigate.call_args_list if call.args[0] <= 2]
                )
                return original_solve(quiz)

            solver.solve = solve
            flow = CourseWorkflow(fresh, solver, dry_run=False)
            stats = flow.run()

        self.assertEqual(flow.last_error, "")
        self.assertEqual(stats.submitted, 1)
        self.assertEqual(old_navigation_before_solves, [[], [], []])
        self.assertEqual([call.args[0] for call in save.call_args_list], [3, 4, 5])
        self.assertEqual(prefix_reads, [(1, 3, 5), (2, 3, 5)])
        self.assertEqual(self.frame.evaluate("[stepSaves, submitClicks, choiceClicks]"),
                         [5, 1, before_choices + 3])
        self.assertEqual(set(fresh._saved_steps), {1, 2, 3, 4, 5})

    def test_dry_run_previews_current_question_without_reading_uncached_prefix(self):
        fresh = self.prepare_saved_prefix()
        fresh.dry_run = True
        solver = sequential.Solver()

        with (
            patch.object(fresh, "_go_question", wraps=fresh._go_question) as navigate,
            patch.object(fresh, "_read_assessment_step", wraps=fresh._read_assessment_step) as read,
        ):
            flow = CourseWorkflow(fresh, solver, dry_run=True)
            stats = flow.run()

        self.assertEqual(flow.last_error, "")
        self.assertEqual(stats.tested, 1)
        self.assertEqual(len(solver.calls), 1)
        self.assertIn("单选题 3", solver.calls[0].questions[0].stem)
        self.assertTrue(all(call.args[0] == 3 for call in navigate.call_args_list))
        self.assertEqual([call.args[0] for call in read.call_args_list], [3])
        self.assertEqual(fresh._deferred_prefix_count, 2)
        self.assertEqual(self.frame.evaluate("[stepSaves, submitClicks, question]"), [2, 0, 3])

    def test_cached_semantic_titles_restore_saved_answers_without_old_navigation(self):
        fresh = self.prepare_saved_prefix()
        # Keep actual rendered components in the DOM, outside the viewport. Their own
        # titles identify the source question even though only question 3 is active.
        self.frame.evaluate("""() => {
          const cache = document.createElement('div');
          cache.style.cssText = 'position:absolute;left:-10000px;top:0;width:800px';
          document.body.append(cache);
          for (const number of [1, 2]) {
            showQuestion(number);
            const widget = article.shadowRoot.querySelector('[sgpulse-type="component"]');
            const title = document.createElement('div');
            title.className = 'component__title-inner';
            title.textContent = '问题 ' + number;
            widget.shadowRoot.prepend(title);
            cache.append(widget);
          }
          showQuestion(3);
        }""")
        fresh.open_assessment()
        self.assertEqual(fresh.assessment_steps(), 5)
        before = self.frame.evaluate("[stepSaves, submitClicks, choiceClicks, question]")

        with patch.object(fresh, "_go_question", wraps=fresh._go_question) as navigate:
            restored = fresh.resume_assessment_steps()

        self.assertEqual(len(restored), 2)
        self.assertEqual([answers for _, answers in restored], [{1: "B"}, {1: "A", 2: "B"}])
        self.assertEqual(set(fresh._saved_steps), {1, 2})
        self.assertEqual(fresh._deferred_prefix_count, 0)
        self.assertTrue(all(call.args[0] == 3 for call in navigate.call_args_list))
        self.assertEqual(self.frame.evaluate("[stepSaves, submitClicks, choiceClicks, question]"), before)

    def test_incomplete_old_answer_blocks_final_submission_after_tail_is_processed(self):
        fresh = self.prepare_saved_prefix()
        self.frame.evaluate("pairs=['A','']")
        solver = sequential.Solver()
        old_navigation_before_first_solve = []
        original_solve = solver.solve

        with patch.object(fresh, "_go_question", wraps=fresh._go_question) as navigate:
            def solve(quiz):
                if not solver.calls:
                    old_navigation_before_first_solve.extend(
                        call.args[0] for call in navigate.call_args_list if call.args[0] <= 2
                    )
                return original_solve(quiz)

            solver.solve = solve
            flow = CourseWorkflow(fresh, solver, dry_run=False)
            stats = flow.run()

        self.assertEqual(stats.errors, 1)
        self.assertIn("已保存题目", flow.last_error)
        self.assertEqual(old_navigation_before_first_solve, [])
        self.assertEqual(len(solver.calls), 3)
        self.assertEqual(self.frame.evaluate("[stepSaves, submitClicks, round]"), [5, 0, 0])

    def test_stop_during_final_prefix_read_prevents_more_navigation_or_submission(self):
        fresh = self.prepare_saved_prefix()
        stop_event = Event()
        fresh.stop_event = stop_event
        solver = sequential.Solver()
        prefix_reads = []
        clicks_after_stop = []
        original_read = fresh._read_assessment_step
        original_click = fresh._click

        def read(number):
            quiz = original_read(number)
            if number <= 2:
                prefix_reads.append((number, len(solver.calls), self.frame.evaluate("stepSaves")))
                stop_event.set()
            return quiz

        def click(locator):
            if stop_event.is_set():
                clicks_after_stop.append(str(locator))
            return original_click(locator)

        with (
            patch.object(fresh, "_read_assessment_step", side_effect=read),
            patch.object(fresh, "_go_question", wraps=fresh._go_question) as navigate,
            patch.object(fresh, "_click", side_effect=click),
        ):
            flow = CourseWorkflow(fresh, solver, dry_run=False, stop_event=stop_event)
            stats = flow.run()

        self.assertTrue(stop_event.is_set())
        self.assertEqual(flow.last_error, "")
        self.assertEqual(stats.errors, 0)
        self.assertEqual(stats.submitted, 0)
        self.assertEqual(len(solver.calls), 3)
        self.assertEqual(prefix_reads, [(1, 3, 5)])
        self.assertNotIn(2, [call.args[0] for call in navigate.call_args_list])
        self.assertEqual(clicks_after_stop, [])
        self.assertEqual(self.frame.evaluate("[stepSaves, submitClicks, question]"), [5, 0, 1])


if __name__ == "__main__":
    unittest.main()
