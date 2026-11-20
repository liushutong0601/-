from __future__ import annotations

import unittest
from threading import Event
from unittest.mock import Mock, patch

from autoquiz.answers import normalize_answer, validate_answers
from autoquiz.browser import BrowserStopped
from autoquiz.kimi import DeepSeekSolver, KimiSolver, SolverError
from autoquiz.models import QuestionType
from autoquiz.parser import parse_quiz


def make_raw(total: int = 3) -> dict:
    return {
        "body_text": (
            f"章节测验 题量：{total} 满分：100.0 "
            "一、单选题（共1题） 二、多选题（共1题） 三、判断题（共1题）"
        ),
        "questions": [
            {
                "number": 1,
                "raw_type": "单选题",
                "stem": "下列哪项正确？",
                "options": [
                    {"letter": "A", "text": "甲"},
                    {"letter": "B", "text": "乙"},
                ],
            },
            {
                "number": 2,
                "raw_type": "多选题",
                "stem": "下列哪些正确？",
                "options": [
                    {"letter": "A", "text": "甲"},
                    {"letter": "B", "text": "乙"},
                    {"letter": "C", "text": "丙"},
                ],
            },
            {
                "number": 3,
                "raw_type": "判断题",
                "stem": "这个说法正确吗？",
                "options": [
                    {"letter": "A", "text": "对"},
                    {"letter": "B", "text": "错"},
                ],
            },
        ],
    }


class ParserTests(unittest.TestCase):
    def test_dynamic_question_count(self) -> None:
        quiz = parse_quiz(make_raw(3))
        self.assertEqual(quiz.declared_total, 3)
        self.assertEqual(quiz.total, 3)
        self.assertEqual(quiz.structural_issues(), [])
        self.assertEqual(quiz.questions[1].kind, QuestionType.MULTIPLE)

    def test_mismatched_total_fails_closed(self) -> None:
        raw = make_raw(4)
        quiz = parse_quiz(raw)
        self.assertTrue(any("不一致" in issue for issue in quiz.structural_issues()))

    def test_unsupported_type_fails_closed(self) -> None:
        raw = make_raw(3)
        raw["questions"][0]["raw_type"] = "填空题"
        quiz = parse_quiz(raw)
        self.assertTrue(any("填空题" in issue for issue in quiz.structural_issues()))

    def test_image_question_fails_closed(self) -> None:
        raw = make_raw(3)
        raw["questions"][0]["has_images"] = True
        quiz = parse_quiz(raw)
        self.assertTrue(any("包含图片" in issue for issue in quiz.structural_issues()))

    def test_missing_question_number_fails_closed(self) -> None:
        raw = make_raw(3)
        raw["questions"][1]["number"] = 3
        quiz = parse_quiz(raw)
        self.assertTrue(any("重复" in issue or "缺题" in issue for issue in quiz.structural_issues()))


class AnswerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.quiz = parse_quiz(make_raw())

    def test_multiple_answer_uses_option_order(self) -> None:
        self.assertEqual(normalize_answer(self.quiz.questions[1], "C,A"), "AC")

    def test_judgement_text_maps_to_actual_option(self) -> None:
        self.assertEqual(normalize_answer(self.quiz.questions[2], "正确"), "A")
        self.assertEqual(normalize_answer(self.quiz.questions[2], "错误"), "B")

    def test_out_of_range_answer_is_rejected(self) -> None:
        self.assertIsNone(normalize_answer(self.quiz.questions[0], "Z"))

    def test_incomplete_answers_are_reported(self) -> None:
        valid, issues = validate_answers(self.quiz, {1: "A", 2: "AC"})
        self.assertEqual(len(valid), 2)
        self.assertEqual(issues, ["第 3 题没有有效答案"])

    def test_kimi_json_response_is_parsed(self) -> None:
        content = '```json\n{"answers":[{"number":1,"answer":"A"},{"number":2,"answer":"AC"},{"number":3,"answer":"B"}]}\n```'
        result = KimiSolver._parse_response(content, self.quiz.questions)
        self.assertEqual(result, {1: "A", 2: "AC", 3: "B"})

    def test_screenshot_question_uses_vision_instead_of_garbled_dom_text(self) -> None:
        raw = make_raw()
        raw["questions"][0]["stem"] = "挴患挲挴情挶"
        raw["questions"][0]["screenshot_b64"] = "ZmFrZS1wbmc="
        quiz = parse_quiz(raw)
        question = quiz.questions[0]
        content = KimiSolver._message_content([question])
        self.assertIsInstance(content, list)
        self.assertIn("data:image/png;base64,ZmFrZS1wbmc=", str(content))
        self.assertNotIn("挴患挲挴情挶", str(content))

    def test_screenshot_can_supply_content_when_dom_stem_is_empty(self) -> None:
        raw = make_raw()
        raw["questions"][0]["stem"] = ""
        raw["questions"][0]["screenshot_b64"] = "ZmFrZS1wbmc="
        quiz = parse_quiz(raw)
        self.assertEqual(quiz.structural_issues(), [])

    def test_kimi_defaults_to_k26_with_thinking_disabled(self) -> None:
        solver = KimiSolver(api_key="sk-test")
        self.assertEqual(solver.model, "kimi-k2.6")
        self.assertEqual(
            solver._request_options(),
            {"extra_body": {"thinking": {"type": "disabled"}}},
        )

    def test_kimi_enforces_three_rpm_request_interval(self) -> None:
        solver = KimiSolver(api_key="sk-test")
        solver._last_request_started = 100.0
        with (
            patch("autoquiz.kimi.time.monotonic", return_value=105.0),
            patch("autoquiz.kimi.time.sleep") as sleep,
        ):
            solver._wait_for_request_slot()
        sleep.assert_called_once_with(16.0)

    def test_rate_limit_429_waits_and_retries_instead_of_stopping(self) -> None:
        solver = KimiSolver(api_key="sk-test")
        solver.request_interval = 0.0
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = '{"answers":[{"number":1,"answer":"A"}]}'
        client = Mock()
        client.chat.completions.create.side_effect = [
            Exception("Error code: 429 - max RPM: 3, please try again after 1 seconds"),
            response,
        ]
        solver._client = Mock(return_value=client)
        logs = []
        solver.logger = logs.append
        with patch("autoquiz.kimi.time.sleep") as sleep:
            result = solver._request([self.quiz.questions[0]])
        self.assertEqual(result, {1: "A"})
        self.assertEqual(client.chat.completions.create.call_count, 2)
        sleep.assert_called_once_with(5.0)
        self.assertTrue(any("自动重试" in message for message in logs))

    def test_deepseek_uses_official_endpoint_and_supports_screenshot_messages(self) -> None:
        solver = DeepSeekSolver(api_key="sk-test")
        self.assertEqual(solver.model, "deepseek-flash")
        client = solver._client()
        self.assertEqual(str(client.base_url).rstrip("/"), "https://api.deepseek.com")
        client.close()

        self.quiz.questions[0].screenshot_b64 = "ZmFrZS1wbmc="
        content = DeepSeekSolver._message_content([self.quiz.questions[0]])
        self.assertIn("data:image/png;base64,ZmFrZS1wbmc=", str(content))

    def test_deepseek_retries_same_request_on_connection_error(self) -> None:
        from openai import APIConnectionError

        solver = DeepSeekSolver(api_key="sk-test")
        client = Mock()
        response = Mock()
        response.choices = [Mock()]
        response.choices[0].message.content = '{"answers":[{"number":1,"answer":"A"}]}'
        request = client.chat.completions.create
        request.side_effect = [APIConnectionError(request=Mock()), response]
        with patch.object(solver, '_client', return_value=client), patch('autoquiz.kimi.time.sleep') as sleep:
            self.assertEqual(solver._request([self.quiz.questions[0]]), {1: 'A'})
        self.assertEqual(request.call_count, 2)
        self.assertEqual(request.call_args_list[0], request.call_args_list[1])
        sleep.assert_called_once_with(2)
        client.close.assert_called_once()

    def test_connection_retry_has_limit_and_hides_proxy_error_details(self) -> None:
        from openai import APIConnectionError

        logs = []
        solver = DeepSeekSolver(api_key="sk-test", logger=logs.append)
        client = Mock()
        failure = APIConnectionError(request=Mock())
        failure.__cause__ = RuntimeError('ProxyError: http://mock-user:private-token@127.0.0.1')
        request = client.chat.completions.create
        request.side_effect = failure
        with patch.object(solver, '_client', return_value=client), patch('autoquiz.kimi.time.sleep') as sleep:
            with self.assertRaisesRegex(SolverError, '代理连接失败') as error:
                solver._request([self.quiz.questions[0]])
        self.assertEqual(request.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 4])
        self.assertNotIn('private-token', ' '.join([*logs, str(error.exception)]))

    def test_stop_during_connection_backoff_prevents_retry(self) -> None:
        from openai import APIConnectionError

        stop_event = Event()
        solver = DeepSeekSolver(api_key="sk-test", stop_event=stop_event, logger=lambda _: stop_event.set())
        client = Mock()
        request = client.chat.completions.create
        request.side_effect = APIConnectionError(request=Mock())
        with patch.object(solver, '_client', return_value=client):
            with self.assertRaises(BrowserStopped):
                solver._request([self.quiz.questions[0]])
        request.assert_called_once()

    def test_non_ascii_api_key_has_clear_error(self) -> None:
        with self.assertRaisesRegex(Exception, "含有中文"):
            KimiSolver(api_key="sk-test-中文")


if __name__ == "__main__":
    unittest.main()
