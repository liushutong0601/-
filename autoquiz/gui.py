from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .browser import BrowserError, BrowserStopped, EdgeBrowser
from .diagnostics import explain_error
from .kimi import DeepSeekSolver, KimiSolver
from .runtime import ensure_debug_edge, log_dir, resource_path
from .workflow import QuizWorkflow


class TaskWorker(QThread):
    log_message = Signal(str)
    task_result = Signal(dict)

    def __init__(self, mode: str, config: dict, parent=None) -> None:
        super().__init__(parent)
        from datetime import datetime

        self.mode = mode
        self.config = config
        self.stop_event = threading.Event()
        output_dir = log_dir()
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        self.log_path = output_dir / f"run-{timestamp}-{mode}.log"
        self.result_path = output_dir / f"run-{timestamp}-{mode}.json"
        self.log_message.connect(self._write_log)

    def _write_log(self, message: str) -> None:
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            self.log_message.emit(f"运行日志：{self.log_path}")
            self.log_message.emit("正在检测软件专用 Edge……")
            browser_state = ensure_debug_edge(self.config["cdp_url"])
            self.log_message.emit(
                "已连接现有专用 Edge。"
                if browser_state == "connected"
                else "已自动启动软件专用 Edge，不会修改你的日常浏览器设置。"
            )

            with EdgeBrowser(self.config["cdp_url"], stop_event=self.stop_event) as browser:
                info = browser.page_info()
                self.log_message.emit(f"已连接页面：{info['title']}")
                self.log_message.emit(f"页面地址：{info['url']}")

                if self.mode == "launch":
                    self.log_message.emit("浏览器已就绪。请登录学习通并进入准备开始答题的小节。")
                    self.task_result.emit({"status": "browser_ready"})
                    return

                if self.mode == "probe":
                    self.task_result.emit({"status": "connected"})
                    return

                if "chaoxing.com" not in info["url"] or "studentstudy" not in info["url"]:
                    raise BrowserError(
                        "未检测到学习通小节页面。请在专用 Edge 中登录学习通，"
                        "打开课程并进入准备开始答题的小节。"
                    )

                if self.mode == "analyze":
                    workflow = QuizWorkflow(browser, solver=None, logger=self.log_message.emit)
                    quiz, issues = workflow.analyze_current()
                    if quiz is None:
                        for issue in issues:
                            self.log_message.emit(issue)
                        self.task_result.emit({"status": "no_quiz"})
                        return
                    self.log_message.emit(
                        f"动态题数：{quiz.declared_total}；实际提取：{quiz.total}；题型：{quiz.type_counts}"
                    )
                    for question in quiz.questions:
                        content_status = (
                            "已获取题目截图，将由 AI 视觉识别"
                            if question.screenshot_b64
                            else question.stem[:80]
                        )
                        self.log_message.emit(
                            f"第 {question.number} 题｜{question.kind.value}｜"
                            f"{len(question.options)} 个选项｜{content_status}"
                        )
                    for issue in issues:
                        self.log_message.emit(f"校验提示：{issue}")
                    self.log_message.emit("当前章节解析完成，未调用 AI、未填写、未提交。")
                    self.task_result.emit({"status": "analyzed", "total": quiz.total})
                    return

                self.log_message.emit(
                    "运行模式：测试模式，不执行真实提交。"
                    if self.config["dry_run"]
                    else "运行模式：真实提交已开启。"
                )
                solver_class = (
                    DeepSeekSolver if self.config.get("provider") == "deepseek" else KimiSolver
                )
                solver = solver_class(
                    api_key=self.config["api_key"],
                    model=self.config["model"],
                    logger=self.log_message.emit,
                )
                workflow = QuizWorkflow(
                    browser,
                    solver,
                    dry_run=self.config["dry_run"],
                    allow_fill=self.config["allow_fill"],
                    logger=self.log_message.emit,
                    stop_event=self.stop_event,
                )
                stats = workflow.run()
                import json
                from dataclasses import asdict

                self.result_path.write_text(
                    json.dumps(asdict(stats), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.log_message.emit(f"章节结果：{self.result_path}")
                if self.stop_event.is_set():
                    status = "stopped"
                else:
                    status = "paused" if workflow.last_error else "finished"
                self.task_result.emit(
                    {
                        "status": status,
                        "summary": stats.summary(),
                        "error": workflow.last_error,
                    }
                )
        except BrowserStopped:
            self.task_result.emit({"status": "stopped"})
        except Exception as exc:
            self.log_message.emit(f"运行失败：{exc}")
            self.task_result.emit({"status": "error", "error": str(exc)})


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"自动搜题 · 学习通章节测验 v{__version__}")
        self.resize(900, 780)
        self.worker: TaskWorker | None = None
        self.last_result: dict = {}
        self.last_log_path: Path | None = None
        self.last_task_mode = "run"
        self.current_error_text = ""
        self._close_when_stopped = False

        self.close_timer = QTimer(self)
        self.close_timer.setSingleShot(True)
        self.close_timer.setInterval(5000)
        self.close_timer.timeout.connect(self.close)

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)

        help_label = QLabel(
            "使用顺序：启动学习通浏览器 → 登录并手动进入起始小节 → 开始答题。\n"
            "开始时会自动检测浏览器和页面；日志始终显示。API Key 仅保留在当前窗口，不写入文件。"
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        settings_group = QGroupBox("AI 设置")
        form = QFormLayout(settings_group)
        self.provider_input = QComboBox()
        self.provider_input.addItem("DeepSeek", "deepseek")
        self.provider_input.addItem("Kimi（Moonshot AI）", "kimi")
        self.current_provider = "deepseek"
        self.api_keys = {
            "deepseek": os.environ.get("DEEPSEEK_API_KEY", ""),
            "kimi": os.environ.get("MOONSHOT_API_KEY", ""),
        }
        self.api_input = QLineEdit(self.api_keys["deepseek"])
        self.api_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_input.setPlaceholderText("填写 DeepSeek 开放平台 API Key")
        self.api_label = QLabel("DeepSeek API Key")
        form.addRow("模型服务商", self.provider_input)
        form.addRow(self.api_label, self.api_input)
        self.provider_input.currentIndexChanged.connect(self.on_provider_changed)
        layout.addWidget(settings_group)

        options_layout = QHBoxLayout()
        self.test_mode_radio = QRadioButton("测试模式（只填写，不提交）")
        self.real_mode_radio = QRadioButton("真实提交模式")
        self.test_mode_radio.setChecked(True)
        self.auto_close_check = QCheckBox("全部完成后 5 秒自动退出软件")
        self.auto_close_check.setChecked(True)
        self.auto_close_check.toggled.connect(self.on_auto_close_toggled)
        options_layout.addWidget(self.test_mode_radio)
        options_layout.addWidget(self.real_mode_radio)
        options_layout.addWidget(self.auto_close_check)
        options_layout.addStretch(1)
        layout.addLayout(options_layout)

        button_layout = QHBoxLayout()
        self.launch_button = QPushButton("启动学习通浏览器")
        self.run_button = QPushButton("开始答题")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.launch_button)
        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.stop_button)
        layout.addLayout(button_layout)

        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setPlaceholderText("运行日志将在这里显示；开始答题后无需再手动测试连接。")
        layout.addWidget(self.log_output, stretch=1)

        self.error_group = QGroupBox("任务未继续，请按以下方法处理")
        error_layout = QVBoxLayout(self.error_group)
        self.error_title_label = QLabel()
        self.error_title_label.setWordWrap(True)
        self.error_cause_label = QLabel()
        self.error_cause_label.setWordWrap(True)
        self.error_solution_label = QLabel()
        self.error_solution_label.setWordWrap(True)
        error_layout.addWidget(self.error_title_label)
        error_layout.addWidget(self.error_cause_label)
        error_layout.addWidget(self.error_solution_label)
        error_buttons = QHBoxLayout()
        self.retry_button = QPushButton("重新检测并继续")
        self.copy_error_button = QPushButton("复制错误信息")
        self.open_log_button = QPushButton("打开完整日志")
        error_buttons.addWidget(self.retry_button)
        error_buttons.addWidget(self.copy_error_button)
        error_buttons.addWidget(self.open_log_button)
        error_layout.addLayout(error_buttons)
        self.error_group.setVisible(False)
        layout.addWidget(self.error_group)

        self.advanced_toggle = QPushButton("高级设置 / 诊断 ▾")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setFlat(True)
        layout.addWidget(self.advanced_toggle)
        self.advanced_panel = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_form = QFormLayout()
        self.cdp_input = QLineEdit("http://127.0.0.1:9222")
        self.model_input = QLineEdit("deepseek-flash")
        advanced_form.addRow("Edge 调试地址", self.cdp_input)
        advanced_form.addRow("模型", self.model_input)
        advanced_layout.addLayout(advanced_form)
        diagnostic_layout = QHBoxLayout()
        self.probe_button = QPushButton("诊断：测试连接")
        self.analyze_button = QPushButton("诊断：解析当前章节")
        diagnostic_layout.addWidget(self.probe_button)
        diagnostic_layout.addWidget(self.analyze_button)
        diagnostic_layout.addStretch(1)
        advanced_layout.addLayout(diagnostic_layout)
        self.advanced_panel.setVisible(False)
        layout.addWidget(self.advanced_panel)

        utility_layout = QHBoxLayout()
        self.readme_button = QPushButton("查看使用说明")
        self.logs_button = QPushButton("打开运行记录")
        utility_layout.addWidget(self.readme_button)
        utility_layout.addWidget(self.logs_button)
        utility_layout.addStretch(1)
        layout.addLayout(utility_layout)

        self.status_label = QLabel("就绪。真实提交默认关闭。")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.launch_button.clicked.connect(lambda: self.start_task("launch"))
        self.probe_button.clicked.connect(lambda: self.start_task("probe"))
        self.analyze_button.clicked.connect(lambda: self.start_task("analyze"))
        self.run_button.clicked.connect(self.confirm_and_run)
        self.stop_button.clicked.connect(self.stop_task)
        self.retry_button.clicked.connect(self.retry_task)
        self.copy_error_button.clicked.connect(self.copy_error)
        self.open_log_button.clicked.connect(self.open_current_log)
        self.readme_button.clicked.connect(self.open_readme)
        self.logs_button.clicked.connect(self.open_logs)
        self.advanced_toggle.toggled.connect(self.toggle_advanced)

    def on_provider_changed(self) -> None:
        self.api_keys[self.current_provider] = self.api_input.text()
        provider = str(self.provider_input.currentData())
        self.current_provider = provider
        self.api_input.setText(self.api_keys.get(provider, ""))
        if provider == "deepseek":
            self.model_input.setText("deepseek-flash")
            self.api_label.setText("DeepSeek API Key")
            self.api_input.setPlaceholderText("填写 DeepSeek 开放平台 API Key")
        else:
            self.model_input.setText("kimi-k2.6")
            self.api_label.setText("Kimi API Key")
            self.api_input.setPlaceholderText("填写 Moonshot AI 开放平台 API Key")

    def append_log(self, message: str) -> None:
        self.log_output.appendPlainText(message)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def confirm_and_run(self) -> None:
        if not self.api_input.text().strip():
            provider_name = "DeepSeek" if self.current_provider == "deepseek" else "Kimi"
            QMessageBox.information(self, "缺少 API Key", f"请先填写 {provider_name} API Key。")
            return
        if self.real_mode_radio.isChecked():
            answer = QMessageBox.warning(
                self,
                "确认开启真实提交",
                "真实提交模式会在答案完整且填写校验通过后自动提交，且无法由软件撤回。\n确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.start_task("run")

    def start_task(self, mode: str) -> None:
        if self.worker is not None and self.worker.isRunning():
            return
        self.close_timer.stop()
        self.last_result = {}
        self.last_task_mode = mode
        self.error_group.setVisible(False)
        config = {
            "cdp_url": self.cdp_input.text().strip(),
            "provider": self.provider_input.currentData(),
            "model": self.model_input.text().strip(),
            "api_key": self.api_input.text().strip(),
            "dry_run": self.test_mode_radio.isChecked(),
            "allow_fill": True,
        }
        task_names = {
            "launch": "启动浏览器",
            "probe": "测试连接",
            "analyze": "解析章节",
            "run": "自动答题",
        }
        self.append_log(f"\n—— 开始任务：{task_names.get(mode, mode)} ——")
        self.status_label.setText("正在自动检测并运行，请查看上方日志……")
        self.worker = TaskWorker(mode, config, self)
        self.last_log_path = self.worker.log_path
        self.worker.log_message.connect(self.append_log)
        self.worker.task_result.connect(self.on_result)
        self.worker.finished.connect(self.on_finished)
        self.worker.start()
        self.set_controls_running(True, mode)

    def set_controls_running(self, running: bool, mode: str = "run") -> None:
        self.launch_button.setEnabled(not running)
        self.probe_button.setEnabled(not running)
        self.analyze_button.setEnabled(not running)
        self.run_button.setEnabled(not running)
        self.stop_button.setEnabled(running and mode == "run")
        self.provider_input.setEnabled(not running)
        self.api_input.setEnabled(not running)
        self.model_input.setEnabled(not running)
        self.cdp_input.setEnabled(not running)
        self.test_mode_radio.setEnabled(not running)
        self.real_mode_radio.setEnabled(not running)

    def stop_task(self) -> None:
        self.close_timer.stop()
        if self.worker is not None:
            self.worker.stop()
            self.status_label.setText("正在停止，请等待当前页面操作结束……")
            self.stop_button.setEnabled(False)

    def retry_task(self) -> None:
        mode = self.last_task_mode if self.last_task_mode in {"launch", "probe", "analyze", "run"} else "run"
        if mode == "run" and not self.api_input.text().strip():
            self.confirm_and_run()
            return
        self.start_task(mode)

    def on_result(self, result: dict) -> None:
        self.last_result = result
        if result.get("summary"):
            self.append_log(result["summary"])
        if result.get("status") in {"error", "paused"}:
            self.show_error(str(result.get("error") or "任务已暂停，请查看日志。"))

    def show_error(self, message: str) -> None:
        guidance = explain_error(message)
        self.current_error_text = (
            f"{guidance.title}\n可能原因：{guidance.cause}\n解决方法：{guidance.solution}"
        )
        self.error_title_label.setText(f"发生了什么：{guidance.title}")
        self.error_cause_label.setText(f"可能原因：{guidance.cause}")
        self.error_solution_label.setText(f"解决方法：{guidance.solution}")
        self.error_group.setVisible(True)

    def should_auto_close(self, result: dict | None = None) -> bool:
        result = self.last_result if result is None else result
        return bool(
            self.auto_close_check.isChecked()
            and result.get("status") == "finished"
            and not self._close_when_stopped
        )

    def on_auto_close_toggled(self, checked: bool) -> None:
        if not checked and self.close_timer.isActive():
            self.close_timer.stop()
            self.status_label.setText("已取消自动退出，软件将保持打开。")

    def on_finished(self) -> None:
        self.set_controls_running(False)
        status = self.last_result.get("status")
        status_messages = {
            "browser_ready": "浏览器已就绪。登录并进入起始小节后，点击“开始答题”。",
            "connected": "浏览器连接正常。",
            "analyzed": "章节解析完成，未调用 AI、未填写、未提交。",
            "no_quiz": "当前小节未识别到章节测验，可开始自动答题继续查找。",
            "finished": "全部小节处理完成。",
            "paused": "任务已暂停，请按错误提示处理后继续。",
            "stopped": "已按你的要求停止，软件保持打开。",
            "error": "运行失败，请按错误提示处理。",
        }
        self.status_label.setText(status_messages.get(status, "任务结束。请根据日志确认结果。"))
        if self.worker is not None:
            self.worker.deleteLater()
            self.worker = None

        if self._close_when_stopped:
            QTimer.singleShot(0, self.close)
            return
        if self.should_auto_close():
            self.append_log("全部任务处理完成，软件将在 5 秒后自动退出；Edge 不会被关闭。")
            self.status_label.setText("全部完成，5 秒后自动退出软件；取消勾选“自动退出”可保持窗口。")
            self.close_timer.start()

    def copy_error(self) -> None:
        QApplication.clipboard().setText(self.current_error_text)
        self.status_label.setText("错误信息已复制。")

    def open_current_log(self) -> None:
        if self.last_log_path and self.last_log_path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_log_path)))
        else:
            self.open_logs()

    def open_logs(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir())))

    def open_readme(self) -> None:
        path = resource_path("README.md")
        if path.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        else:
            QMessageBox.information(self, "使用说明", "未找到 README.md，请重新解压完整软件包。")

    def toggle_advanced(self, checked: bool) -> None:
        self.advanced_panel.setVisible(checked)
        self.advanced_toggle.setText("高级设置 / 诊断 ▴" if checked else "高级设置 / 诊断 ▾")

    def closeEvent(self, event) -> None:
        self.close_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self._close_when_stopped = True
            self.worker.stop()
            self.status_label.setText("正在安全停止当前任务，完成后软件将退出……")
            self.stop_button.setEnabled(False)
            event.ignore()
            return
        event.accept()


def run_app(*, smoke_test: bool = False) -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    if smoke_test:
        QTimer.singleShot(1200, window.close)
    app.exec()
