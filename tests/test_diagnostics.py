from __future__ import annotations

import unittest

from autoquiz.diagnostics import explain_error


class DiagnosticsTests(unittest.TestCase):
    def test_api_key_error_has_recovery_action(self) -> None:
        guidance = explain_error("Error code: 401 - Invalid Authentication")
        self.assertIn("API Key", guidance.title)
        self.assertIn("重新复制", guidance.solution)

    def test_model_error_names_supported_defaults(self) -> None:
        guidance = explain_error("404 - Not found the model custom-model")
        self.assertIn("kimi-k2.6", guidance.solution)
        self.assertIn("deepseek-flash", guidance.solution)

    def test_rate_limit_explains_wait_and_retry(self) -> None:
        guidance = explain_error("Error code: 429 - max RPM reached")
        self.assertIn("等待", guidance.solution)
        self.assertIn("重新检测并继续", guidance.solution)

    def test_edge_error_does_not_tell_user_to_edit_daily_browser(self) -> None:
        guidance = explain_error("无法连接 Edge")
        self.assertIn("软件专用 Edge", guidance.solution)
        self.assertIn("启动学习通浏览器", guidance.solution)

    def test_submit_error_prevents_blind_duplicate_submission(self) -> None:
        guidance = explain_error("提交后未识别到结果页")
        self.assertIn("不要重复点击提交", guidance.solution)

    def test_unknown_error_still_has_next_step(self) -> None:
        guidance = explain_error("unexpected")
        self.assertTrue(guidance.cause)
        self.assertIn("完整日志", guidance.solution)


if __name__ == "__main__":
    unittest.main()
