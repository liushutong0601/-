"""Windows 课程助手的白底主题。"""

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionComboBox


class ChevronComboBox(QComboBox):
    """使用主题箭头，避开原生样式与自定义圆角叠加时的箭头缺失。"""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox, option, QStyle.SubControl.SC_ComboBoxArrow, self
        )
        x, y = arrow.center().x(), arrow.center().y()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor('#536b75' if self.isEnabled() else '#8e9fa5'), 1.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPolyline(QPolygonF([QPointF(x - 4, y - 2), QPointF(x, y + 2), QPointF(x + 4, y - 2)]))


WINDOW_STYLE = """
QWidget { color: #192a31; }
#workspace, QMainWindow { background: #ffffff; }
QScrollArea { background: #ffffff; border: none; }
QFrame#settingsCard, QFrame#logCard, QFrame#actionBar, QFrame#advancedPanel {
    background: #ffffff;
    border: 1px solid #dce6e7;
    border-radius: 17px;
}
QLabel#pageHeading { font-size: 28px; font-weight: 700; }
QLabel[role="sectionHeading"] { font-size: 17px; font-weight: 700; }
QLabel[role="fieldLabel"] { font-weight: 600; color: #354a52; }
QLabel[role="secondary"] { color: #50646d; }
QLabel#activityBadge {
    color: #17624f; background: #eff8f3;
    border: 1px solid #d5eae1; border-radius: 14px;
    padding: 5px 12px; font-weight: 600;
}
QLabel#activityBadge[tone="working"] { color: #1f546d; background: #eef5f8; border-color: #d9e8f0; }
QLabel#activityBadge[tone="attention"] { color: #815215; background: #fff5e8; border-color: #efdfc8; }

QComboBox, QLineEdit, QSpinBox {
    background: #fbfcfc; border: 1px solid #dbe5e7;
    border-radius: 10px; padding: 6px 9px; min-height: 23px;
    selection-background-color: #dcefe8; selection-color: #142f2a;
}
QComboBox::drop-down { width: 26px; border: none; }
QComboBox::down-arrow { image: none; }
QComboBox QAbstractItemView {
    background: #ffffff; color: #192a31; border: 1px solid #cbdadb;
    selection-background-color: #e3f2ed; selection-color: #1a3933;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #438877; }
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled { background: #f3f6f6; color: #53666d; }

QPushButton, QToolButton {
    background: #ffffff; border: 1px solid #d6e2e4;
    border-radius: 10px; padding: 8px 12px; font-weight: 600;
}
QPushButton:hover, QToolButton:hover { background: #f0f6f4; border-color: #bbd3cc; }
QPushButton:focus, QToolButton:focus { border-color: #438877; }
QPushButton:pressed, QToolButton:pressed { background: #e2f1ea; }
QPushButton:disabled, QToolButton:disabled { color: #687980; background: #f4f6f7; border-color: #e2e9eb; }
QPushButton#runButton { background: #116757; color: #ffffff; border-color: #116757; }
QPushButton#runButton:hover { background: #0d584a; }
QPushButton#runButton:focus { border-color: #82c6b7; }
QPushButton#runButton:pressed { background: #0a4a3e; }
QPushButton#runButton:disabled { background: #e7efed; border-color: #dae5e2; color: #526c64; }
QPushButton#stopButton:enabled { color: #9c3337; }
QPushButton[role="utility"] { border-color: transparent; padding: 5px 7px; color: #46616b; }
QPushButton[role="utility"]:focus { border-color: #438877; }

QFrame#modePicker { background: #f0f5f3; border: 1px solid #e1ebe8; border-radius: 11px; }
QPushButton[role="mode"] { border: 1px solid transparent; background: transparent; padding: 6px 8px; }
QPushButton[role="mode"]:checked { background: #ffffff; border-color: #d7e7e0; color: #116757; }
QPushButton[role="mode"]:focus { border-color: #438877; }

QTextEdit#runLog {
    background: #f7f9fa; border: 1px solid #e3ebed; border-radius: 12px;
    padding: 9px; selection-background-color: #dcefe8; selection-color: #142f2a;
}
QGroupBox#errorGroup {
    background: #fffaf4; border: 1px solid #f0dfc8; border-radius: 13px;
    margin-top: 11px; padding: 12px 3px 3px; font-weight: 600;
}
QGroupBox#errorGroup::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #794720; }
QCheckBox { spacing: 6px; }
QMessageBox { background: #ffffff; }
QToolTip { background: #ffffff; color: #192a31; border: 1px solid #cbdadb; padding: 5px; }
"""
