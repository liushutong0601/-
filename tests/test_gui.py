from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from autoquiz.gui import MainWindow


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


if __name__ == "__main__":
    unittest.main()
