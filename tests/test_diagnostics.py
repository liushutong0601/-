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
        self.assertIn("启动专用浏览器", guidance.solution)

    def test_api_connection_error_points_to_network_and_resume(self) -> None:
        for message in ('DeepSeek API 调用失败：Connection error.', 'DeepSeek API 连接失败（代理连接失败）。'):
            guidance = explain_error(message)
            self.assertEqual(guidance.title, '无法连接 AI 服务')
            self.assertIn('代理', guidance.solution)
            self.assertIn('重新检测并继续', guidance.solution)

    def test_submit_error_prevents_blind_duplicate_submission(self) -> None:
        guidance = explain_error("提交后未识别到结果页")
        self.assertIn("不要重复点击提交", guidance.solution)

    def test_confirmed_full_score_is_explained_as_completed(self) -> None:
        guidance=explain_error("当前测验已显示 100% 成绩，无需重复提交。")
        self.assertEqual(guidance.title,"当前测验已满分")
        self.assertIn("下一待完成节点",guidance.solution)

    def test_unknown_error_still_has_next_step(self) -> None:
        guidance = explain_error("unexpected")
        self.assertTrue(guidance.cause)
        self.assertIn("完整日志", guidance.solution)

    def test_mid_attempt_recovery_error_is_not_explained_as_slow_network(self):
        guidance=explain_error("本轮已有解锁题目，前面题目的保存状态未知。")
        self.assertEqual(guidance.title,'中途续答状态未核实')
        self.assertIn('最后一个已解锁',guidance.solution)
        self.assertNotIn('网络',guidance.solution)


if __name__ == "__main__":
    unittest.main()
