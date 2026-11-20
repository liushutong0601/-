from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import QDateTime, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QTextBlockFormat, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .browser import BrowserError, BrowserStopped, EdgeBrowser
from .diagnostics import explain_error
from .kimi import DeepSeekSolver, KimiSolver
from .runtime import ensure_debug_edge, log_dir, resource_path
from .workflow import QuizWorkflow
from .course import CourseWorkflow
from .netacad import NetAcadBrowser
from .theme import ChevronComboBox, WINDOW_STYLE


PLATFORM_HOMEPAGES = {
    "chaoxing": "https://v8.chaoxing.com/",
    "netacad": "https://www.netacad.com/",
}


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
            platform = self.config.get("platform", "chaoxing")
            homepage = PLATFORM_HOMEPAGES[platform] if self.mode == "launch" else None
            browser_state = ensure_debug_edge(self.config["cdp_url"], start_url=homepage)
            self.log_message.emit(
                "已连接现有专用 Edge。"
                if browser_state == "connected"
                else "已自动启动软件专用 Edge，不会修改你的日常浏览器设置。"
            )

            if homepage is not None:
                if browser_state == "connected":
                    with EdgeBrowser(self.config["cdp_url"], stop_event=self.stop_event) as browser:
                        browser.open_homepage(homepage)
                self.log_message.emit(f"平台首页：{homepage}。请登录并进入准备处理的课程小节。")
                self.task_result.emit({"status": "browser_ready"})
                return

            netacad = platform == "netacad"
            browser_type = NetAcadBrowser if netacad else EdgeBrowser
            options = {"stop_event": self.stop_event}
            if browser_type is NetAcadBrowser:
                options["dry_run"] = self.mode != "run" or self.config["dry_run"]
            with browser_type(self.config["cdp_url"], **options) as browser:
                info = browser.page_info()
                if netacad:
                    self.log_message.emit("已连接 NetAcad 课程页面。")
                else:
                    self.log_message.emit(f"已连接页面：{info['title']}")
                    self.log_message.emit(f"页面地址：{info['url']}")

                if self.mode == "probe":
                    self.task_result.emit({"status": "connected"})
                    return

                if not netacad and ("chaoxing.com" not in info["url"] or "studentstudy" not in info["url"]):
                    raise BrowserError(
                        "未检测到学习通小节页面。请在专用 Edge 中登录学习通，"
                        "打开课程并进入准备开始答题的小节。"
                    )

                if self.mode == "analyze":
                    if netacad:
                        from .netacad_probe import STRUCTURE_JS
                        node = browser.current_node()
                        structure = browser._frame().evaluate(STRUCTURE_JS)
                        self.log_message.emit(
                            f"节点类型 {node.kind.value}，状态 {node.status}；"
                            f"可见选择控件 {structure['visible_choice_controls']}，"
                            f"配对类别 {structure['visible_matching_categories']}。"
                        )
                        self.log_message.emit("只读诊断完成，未开始考试、未调用 AI、未填写或提交。")
                        self.task_result.emit({"status": "analyzed"})
                        return
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
                    stop_event=self.stop_event,
                )
                workflow_type = CourseWorkflow if netacad else QuizWorkflow
                workflow_options = ({"max_retries": self.config.get("max_retries", 3)}
                                    if netacad else {"allow_fill": self.config["allow_fill"]})
                workflow = workflow_type(
                    browser,
                    solver,
                    dry_run=self.config["dry_run"],
                    logger=self.log_message.emit,
                    stop_event=self.stop_event,
                    **workflow_options,
                )
                stats = workflow.run()
                import json
                from dataclasses import asdict

                result_data = asdict(stats)
                if netacad:
                    # NetAcad 结果不持久化课程标题、节点标识或题目内容。
                    result_data.pop("results", None)
                self.result_path.write_text(
                    json.dumps(result_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                self.log_message.emit(f"章节结果：{self.result_path}")
                if self.stop_event.is_set():
                    status = "stopped"
                else:
                    status = "paused" if workflow.last_error else "finished"
                    if netacad and not workflow.last_error and workflow.preview_ready:
                        status = "preview"
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
        self.setWindowTitle(f"自动搜题 · 课程助手 v{__version__}")
        self.setMinimumSize(780, 500)
        screen = self.screen().availableGeometry()
        self.resize(min(1040, screen.width()), min(715, screen.height() - 40))
        self.setStyleSheet(WINDOW_STYLE)
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

        self.workspace_scroll = QScrollArea()
        self.workspace_scroll.setWidgetResizable(True)
        self.workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root = QWidget(objectName="workspace")
        self.workspace_scroll.setWidget(root)
        self.setCentralWidget(self.workspace_scroll)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(28, 22, 28, 17)
        layout.setSpacing(14)

        header = QHBoxLayout()
        intro = QVBoxLayout()
        intro.setSpacing(3)
        heading = QLabel("课程助手", objectName="pageHeading")
        intro.addWidget(heading)
        intro.addWidget(self._label("选择课程平台，启动专用浏览器后登录课程。", "secondary"))
        header.addLayout(intro, 1)
        self.activity_badge = QLabel("就绪", objectName="activityBadge")
        header.addWidget(self.activity_badge, alignment=Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(header)

        panel_row = QHBoxLayout()
        panel_row.setSpacing(16)
        settings_group = QFrame(objectName="settingsCard")
        settings_group.setMinimumWidth(310)
        settings_layout = QVBoxLayout(settings_group)
        settings_layout.setContentsMargins(17, 16, 17, 17)
        settings_layout.setSpacing(9)
        settings_layout.addWidget(self._label("课程与模型", "sectionHeading"))
        form = QFormLayout()
        form.setContentsMargins(0, 3, 0, 0)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setVerticalSpacing(6)
        self.platform_input = ChevronComboBox()
        self.platform_input.addItem("学习通", "chaoxing")
        self.platform_input.addItem("Cisco NetAcad（网页适配预览）", "netacad")
        form.addRow(self._label("课程平台", "fieldLabel", self.platform_input), self.platform_input)
        self.provider_input = ChevronComboBox()
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
        self.api_input.setToolTip("API Key 保存在当前窗口，不写入文件。")
        self.api_label = self._label("DeepSeek API Key", "fieldLabel", self.api_input)
        self.api_visibility_button = QToolButton()
        self.api_visibility_button.setText("显示")
        self.api_visibility_button.setAccessibleName("显示 API Key")
        self.api_visibility_button.setCheckable(True)
        self.api_visibility_button.toggled.connect(self._set_api_key_visible)
        key_row = QHBoxLayout()
        key_row.setSpacing(6)
        key_row.addWidget(self.api_input, 1)
        key_row.addWidget(self.api_visibility_button)
        form.addRow(self._label("模型服务商", "fieldLabel", self.provider_input), self.provider_input)
        form.addRow(self.api_label, key_row)
        self.provider_input.currentIndexChanged.connect(self.on_provider_changed)
        settings_layout.addLayout(form)
        settings_layout.addWidget(self._label("答题模式", "fieldLabel"))

        mode_picker = QFrame(objectName="modePicker")
        mode_layout = QHBoxLayout(mode_picker)
        mode_layout.setContentsMargins(3, 3, 3, 3)
        mode_layout.setSpacing(3)
        self.mode_group = QButtonGroup(self)
        self.test_mode_radio = QPushButton("测试模式")
        self.real_mode_radio = QPushButton("真实提交模式")
        for button in (self.test_mode_radio, self.real_mode_radio):
            button.setProperty("role", "mode")
            button.setCheckable(True)
            self.mode_group.addButton(button)
            mode_layout.addWidget(button, 1)
        self.test_mode_radio.setChecked(True)
        settings_layout.addWidget(mode_picker)
        self.mode_hint_label = self._label("", "secondary")
        settings_layout.addWidget(self.mode_hint_label)
        settings_layout.addStretch(1)
        panel_row.addWidget(settings_group, 3)

        log_card = QFrame(objectName="logCard")
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(17, 16, 17, 17)
        log_layout.setSpacing(10)
        log_layout.addWidget(self._label("运行日志", "sectionHeading"))
        self.log_output = QTextEdit(objectName="runLog")
        self.log_output.setReadOnly(True)
        self.log_output.setAcceptRichText(False)
        self.log_output.setMinimumHeight(190)
        self.log_output.setPlaceholderText("浏览器连接、答题进度和错误提示会显示在这里。")
        log_layout.addWidget(self.log_output, 1)
        panel_row.addWidget(log_card, 5)
        layout.addLayout(panel_row, 1)

        actions = QFrame(objectName="actionBar")
        button_layout = QHBoxLayout(actions)
        button_layout.setContentsMargins(12, 10, 12, 10)
        button_layout.setSpacing(8)
        self.launch_button = QPushButton("启动专用浏览器")
        self.run_button = QPushButton("开始答题", objectName="runButton")
        self.stop_button = QPushButton("停止", objectName="stopButton")
        self.stop_button.setEnabled(False)
        button_layout.addWidget(self.launch_button)
        button_layout.addWidget(self.run_button)
        button_layout.addWidget(self.stop_button)
        button_layout.addStretch(1)
        self.homepage_label = self._label("", "secondary")
        self.homepage_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        button_layout.addWidget(self.homepage_label)
        layout.addWidget(actions)

        self.error_group = QGroupBox("需要处理", objectName="errorGroup")
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

        self.advanced_toggle = QPushButton("展开高级设置 / 诊断")
        self.advanced_toggle.setCheckable(True)
        self.advanced_panel = QFrame(objectName="advancedPanel")
        advanced_layout = QVBoxLayout(self.advanced_panel)
        advanced_layout.setContentsMargins(14, 12, 14, 12)
        advanced_form = QFormLayout()
        self.cdp_input = QLineEdit("http://127.0.0.1:9222")
        self.model_input = QLineEdit("deepseek-flash")
        self.retries_input = QSpinBox()
        self.retries_input.setRange(0, 5)
        self.retries_input.setValue(3)
        self.retries_input.setSuffix(" 次（首次答题之外）")
        advanced_form.addRow("Edge 调试地址", self.cdp_input)
        advanced_form.addRow("模型", self.model_input)
        advanced_form.addRow("NetAcad 最多重做", self.retries_input)
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
        for button in (self.advanced_toggle, self.readme_button, self.logs_button):
            button.setProperty("role", "utility")
            utility_layout.addWidget(button)
        utility_layout.addStretch(1)
        self.auto_close_check = QCheckBox("完成后 5 秒自动退出")
        self.auto_close_check.setChecked(True)
        self.auto_close_check.toggled.connect(self.on_auto_close_toggled)
        utility_layout.addWidget(self.auto_close_check)
        layout.addLayout(utility_layout)

        self.status_label = self._label("真实提交默认关闭。", "secondary")
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
        self.platform_input.currentIndexChanged.connect(self.update_mode_guidance)
        self.test_mode_radio.toggled.connect(self.update_mode_guidance)
        self.real_mode_radio.toggled.connect(self.update_mode_guidance)
        self.update_mode_guidance()

    @staticmethod
    def _label(text: str, role: str, buddy: QWidget | None = None) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", role)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        if buddy is not None:
            label.setBuddy(buddy)
        return label

    def _set_activity(self, text: str, tone: str = "ready") -> None:
        self.activity_badge.setText(text)
        self.activity_badge.setProperty("tone", tone)
        style = self.activity_badge.style()
        style.unpolish(self.activity_badge)
        style.polish(self.activity_badge)

    def _set_api_key_visible(self, visible: bool) -> None:
        self.api_input.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        action = "隐藏" if visible else "显示"
        self.api_visibility_button.setText(action)
        self.api_visibility_button.setAccessibleName(f"{action} API Key")

    def update_mode_guidance(self) -> None:
        platform = self.platform_input.currentData()
        netacad = platform == 'netacad'
        preview = self.test_mode_radio.isChecked()
        host = PLATFORM_HOMEPAGES[platform].removeprefix('https://').rstrip('/')
        self.homepage_label.setText(f"浏览器启动页  {host}")
        if netacad:
            self.run_button.setText('预览答题（不保存）' if preview else '开始答题并提交')
            text = (
                'NetAcad 测试模式：逐题考试只预览当前题，不保存、不解锁后续题。'
                '要继续完成全部题目，请选择“真实提交模式”。'
                if preview else
                'NetAcad 真实提交：逐题保存并解锁后续题目，最后自动确认和提交。'
                '点击开始后会先弹出本次运行确认。'
            )
        else:
            self.run_button.setText('开始答题')
            text = ('学习通测试模式：填写并核对答案，不提交；会继续查找后续小节。' if preview else
                    '学习通真实提交：答案完整并核对通过后自动提交，开始前会弹出本次运行确认。')
        self.mode_hint_label.setText(text)

    def on_provider_changed(self) -> None:
        self.api_visibility_button.setChecked(False)
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
        cursor = QTextCursor(self.log_output.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self.log_output.document().isEmpty():
            cursor.insertBlock()
        spacing = QTextBlockFormat()
        spacing.setTopMargin(2)
        spacing.setBottomMargin(4)
        cursor.setBlockFormat(spacing)
        metadata = QTextCharFormat()
        metadata.setForeground(QColor('#536b75'))
        cursor.insertText(QDateTime.currentDateTime().toString('HH:mm:ss') + '  ', metadata)
        content = QTextCharFormat()
        content.setForeground(QColor('#192a31'))
        cursor.insertText(message.lstrip('\n'), content)
        scrollbar = self.log_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def confirm_and_run(self) -> None:
        if not self.api_input.text().strip():
            provider_name = "DeepSeek" if self.current_provider == "deepseek" else "Kimi"
            QMessageBox.information(self, "缺少 API Key", f"请先填写 {provider_name} API Key。")
            return
        if self.real_mode_radio.isChecked():
            extra = (
                f"\nNetAcad 会启动计时考试，未满分时最多再重做 {self.retries_input.value()} 次。"
                "逐题解锁考试会先保存当前题，再解锁下一题；测试模式不会执行单题保存。"
                "未知题型、外部实验或结果不明确时会暂停。"
                if self.platform_input.currentData() == "netacad" else ""
            )
            answer = QMessageBox.warning(
                self,
                "确认开启真实提交",
                "真实提交模式会在答案完整且填写校验通过后自动提交，且无法由软件撤回。" + extra + "\n确定继续吗？",
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
            "platform": self.platform_input.currentData(),
            "max_retries": self.retries_input.value(),
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
        self._set_activity("运行中", "working")
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
        self.platform_input.setEnabled(not running)
        self.retries_input.setEnabled(not running)
        self.api_input.setEnabled(not running)
        self.api_visibility_button.setEnabled(not running)
        if running:
            self.api_visibility_button.setChecked(False)
        self.model_input.setEnabled(not running)
        self.cdp_input.setEnabled(not running)
        self.test_mode_radio.setEnabled(not running)
        self.real_mode_radio.setEnabled(not running)

    def stop_task(self) -> None:
        self.close_timer.stop()
        if self.worker is not None:
            self.worker.stop()
            self.status_label.setText("正在停止，请等待当前页面操作结束……")
            self._set_activity("正在停止", "working")
            self.stop_button.setEnabled(False)

    def retry_task(self) -> None:
        mode = self.last_task_mode if self.last_task_mode in {"launch", "probe", "analyze", "run"} else "run"
        if mode == "run":
            self.confirm_and_run()
            return
        self.start_task(mode)

    def on_result(self, result: dict) -> None:
        self.last_result = result
        if result.get("summary"):
            self.append_log(result["summary"])
        if result.get('status') == 'preview':
            self.append_log('本次仅完成测试预览，未完成整份测验。'
                            '逐题考试需要保存当前题才能解锁下一题；'
                            '要继续全部题目，请选择“真实提交模式”，再点击“开始答题并提交”并确认。')
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
        self._set_activity("需要处理", "attention")

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
            "browser_ready": "请在专用浏览器登录并进入起始小节，然后按所选模式运行。",
            "connected": "浏览器连接正常。",
            "analyzed": "章节解析完成，未调用 AI、未填写、未提交。",
            "no_quiz": "当前小节未识别到章节测验，可开始自动答题继续查找。",
            "finished": "全部小节处理完成。",
            "preview": "测试预览完成，未完成整份测验。请保留当前页面；要继续全部题目，选择“真实提交模式”，再点击“开始答题并提交”并确认。",
            "paused": "任务已暂停，请按错误提示处理后继续。",
            "stopped": "已按你的要求停止，软件保持打开。",
            "error": "运行失败，请按错误提示处理。",
        }
        self.status_label.setText(status_messages.get(status, "任务结束。请根据日志确认结果。"))
        activity = {
            "browser_ready": "浏览器已就绪",
            "connected": "浏览器已就绪",
            "finished": "已完成",
            "preview": "预览完成",
            "stopped": "已停止",
        }
        if status in {"paused", "error"}:
            self._set_activity("需要处理", "attention")
        else:
            self._set_activity(activity.get(status, "就绪"))
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
        self.advanced_toggle.setText("收起高级设置 / 诊断" if checked else "展开高级设置 / 诊断")
        if checked:
            QTimer.singleShot(0, lambda: self.workspace_scroll.ensureWidgetVisible(self.advanced_panel, 0, 8))

    def closeEvent(self, event) -> None:
        self.close_timer.stop()
        if self.worker is not None and self.worker.isRunning():
            self._close_when_stopped = True
            self.worker.stop()
            self.status_label.setText("正在安全停止当前任务，完成后软件将退出……")
            self._set_activity("正在停止", "working")
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
