from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox

from autoquiz.gui import MainWindow, TaskWorker


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.window = MainWindow()

    def tearDown(self) -> None:
        self.window.close_timer.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.window = None

    def test_main_interface_is_simple_and_log_is_always_available(self) -> None:
        self.window.show()
        self.assertTrue(self.window.auto_close_check.isChecked())
        self.assertFalse(self.window.real_mode_radio.isChecked())
        self.assertTrue(self.window.log_output.isVisible())
        self.assertFalse(self.window.advanced_panel.isVisible())
        self.assertFalse(self.window.error_group.isVisible())

    def test_launch_opens_selected_platform_in_dedicated_edge(self) -> None:
        cdp_url = 'http://127.0.0.1:9222'
        with tempfile.TemporaryDirectory() as log_path:
            for platform, url in (('chaoxing', 'https://v8.chaoxing.com/'), ('netacad', 'https://www.netacad.com/')):
                for browser_state in ('launched', 'connected'):
                    with (
                        self.subTest(platform=platform, browser_state=browser_state),
                        patch('autoquiz.gui.log_dir', return_value=Path(log_path)),
                        patch('autoquiz.gui.ensure_debug_edge', return_value=browser_state) as launch,
                        patch('autoquiz.gui.EdgeBrowser') as edge,
                    ):
                        worker = TaskWorker('launch', {'cdp_url': cdp_url, 'platform': platform})
                        results = []
                        worker.task_result.connect(results.append)
                        worker.run()
                        launch.assert_called_once_with(cdp_url, start_url=url)
                        if browser_state == 'connected':
                            edge.return_value.__enter__.return_value.open_homepage.assert_called_once_with(url)
                        else:
                            edge.assert_not_called()
                        self.assertEqual(results, [{'status': 'browser_ready'}])

    def test_error_guidance_is_visible_and_actionable(self) -> None:
        self.window.show_error("Error code: 429 - max RPM reached")
        self.assertFalse(self.window.error_group.isHidden())
        self.assertIn("可能原因", self.window.error_cause_label.text())
        self.assertIn("等待", self.window.error_solution_label.text())

    def test_only_normal_completion_can_auto_close(self) -> None:
        self.assertTrue(self.window.should_auto_close({"status": "finished"}))
        self.assertFalse(self.window.should_auto_close({"status": "paused"}))
        self.assertFalse(self.window.should_auto_close({"status": "error"}))
        self.assertFalse(self.window.should_auto_close({"status": "stopped"}))
        self.assertFalse(self.window.should_auto_close({"status": "preview"}))
        self.window.auto_close_check.setChecked(False)
        self.assertFalse(self.window.should_auto_close({"status": "finished"}))

    def test_finished_result_starts_cancellable_five_second_timer(self) -> None:
        self.window.last_result = {"status": "finished"}
        self.window.on_finished()
        self.assertTrue(self.window.close_timer.isActive())
        self.assertEqual(self.window.close_timer.interval(), 5000)
        self.window.auto_close_check.setChecked(False)
        self.assertFalse(self.window.close_timer.isActive())

    def test_paused_result_keeps_window_open(self) -> None:
        self.window.last_result = {"status": "paused", "error": "提交后未识别到结果页"}
        self.window.on_finished()
        self.assertFalse(self.window.close_timer.isActive())
        self.assertFalse(self.window.isVisible())

    def test_netacad_defaults_and_retry_keep_real_submission_confirmation(self) -> None:
        self.window.platform_input.setCurrentIndex(1)
        self.assertEqual(self.window.platform_input.currentData(), 'netacad')
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertEqual(self.window.retries_input.value(), 3)
        self.window.last_task_mode = 'run'
        with patch.object(self.window, 'confirm_and_run') as confirm, patch.object(self.window, 'start_task') as start:
            self.window.retry_task()
            confirm.assert_called_once()
            start.assert_not_called()

    def test_mode_guidance_follows_platform_and_mode_without_enabling_submission(self) -> None:
        self.window.show()
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertEqual(self.window.run_button.text(), "开始答题")
        self.assertEqual(self.window.test_mode_radio.text(), "测试模式")
        self.assertIn("v8.chaoxing.com", self.window.homepage_label.text())

        self.window.platform_input.setCurrentIndex(1)
        self.assertTrue(self.window.mode_hint_label.isVisible())
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertFalse(self.window.real_mode_radio.isChecked())
        self.assertEqual(self.window.run_button.text(), "预览答题（不保存）")
        self.assertEqual(self.window.test_mode_radio.text(), "测试模式")
        self.assertIn("www.netacad.com", self.window.homepage_label.text())
        self.assertIn("逐题考试只预览当前题，不保存、不解锁后续题", self.window.mode_hint_label.text())
        self.assertIn("真实提交模式", self.window.mode_hint_label.text())

        self.window.real_mode_radio.setChecked(True)
        self.assertEqual(self.window.run_button.text(), "开始答题并提交")
        for instruction in ("保存", "解锁", "确认"):
            self.assertIn(instruction, self.window.mode_hint_label.text())

        self.window.test_mode_radio.setChecked(True)
        self.assertEqual(self.window.run_button.text(), "预览答题（不保存）")
        self.window.platform_input.setCurrentIndex(0)
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertEqual(self.window.run_button.text(), "开始答题")
        self.assertIn("v8.chaoxing.com", self.window.homepage_label.text())
        self.assertNotIn("逐题考试只预览当前题", self.window.mode_hint_label.text())

    def test_api_key_visibility_resets_when_switching_provider_or_running(self) -> None:
        self.window.api_input.setText('synthetic-test-key')
        self.assertEqual(self.window.api_input.echoMode(), QLineEdit.EchoMode.Password)
        self.window.api_visibility_button.click()
        self.assertEqual(self.window.api_input.echoMode(), QLineEdit.EchoMode.Normal)
        self.window.provider_input.setCurrentIndex(1)
        self.assertEqual(self.window.api_input.echoMode(), QLineEdit.EchoMode.Password)
        self.window.provider_input.setCurrentIndex(0)
        self.assertEqual(self.window.api_input.text(), 'synthetic-test-key')
        self.window.api_visibility_button.click()
        self.window.set_controls_running(True)
        self.assertEqual(self.window.api_input.echoMode(), QLineEdit.EchoMode.Password)
        self.assertFalse(self.window.api_visibility_button.isEnabled())

    def test_netacad_preview_explains_incomplete_exam_and_keeps_window_open(self) -> None:
        self.window.show()
        self.window.platform_input.setCurrentIndex(1)
        self.window.set_controls_running(True)
        with patch.object(self.window, "start_task") as start:
            self.window.on_result({"status": "preview"})
            self.window.on_finished()
            start.assert_not_called()

        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.window.auto_close_check.isChecked())
        self.assertFalse(self.window.close_timer.isActive())
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertFalse(self.window.real_mode_radio.isChecked())
        self.assertTrue(self.window.run_button.isEnabled())
        self.assertTrue(self.window.error_group.isHidden())
        for instruction in ("测试预览", "真实提交模式", "未完成整份测验"):
            self.assertIn(instruction, self.window.status_label.text())
        self.assertIn("真实提交模式", self.window.log_output.toPlainText())

    def test_netacad_real_submission_requires_explicit_confirmation(self) -> None:
        self.window.platform_input.setCurrentIndex(1)
        self.window.api_input.setText("synthetic-test-key")
        self.window.real_mode_radio.setChecked(True)
        for answer in (QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes):
            with self.subTest(answer=answer):
                with patch("autoquiz.gui.QMessageBox.warning", return_value=answer) as warning, patch.object(
                    self.window, "start_task"
                ) as start:
                    self.window.confirm_and_run()
                    warning.assert_called_once()
                    self.assertEqual(warning.call_args.args[-1], QMessageBox.StandardButton.No)
                    if answer == QMessageBox.StandardButton.Yes:
                        start.assert_called_once_with("run")
                    else:
                        start.assert_not_called()

    def test_netacad_preview_starts_without_changing_to_real_submission(self) -> None:
        self.window.platform_input.setCurrentIndex(1)
        self.window.api_input.setText("synthetic-test-key")
        with patch("autoquiz.gui.QMessageBox.warning") as warning, patch.object(self.window, "start_task") as start:
            self.window.confirm_and_run()
            warning.assert_not_called()
            start.assert_called_once_with("run")
        self.assertTrue(self.window.test_mode_radio.isChecked())
        self.assertFalse(self.window.real_mode_radio.isChecked())


if __name__ == "__main__":
    unittest.main()
