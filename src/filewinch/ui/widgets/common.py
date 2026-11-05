"""Small widgets used on several pages."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QGuiApplication
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy,
                             QToolButton, QVBoxLayout, QWidget)


def title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("PageTitle")
    return lab


def hint(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Hint")
    lab.setWordWrap(True)
    return lab


def button(text: str, primary: bool = False, big: bool = False) -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if big:
        b.setProperty("big", True)
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class Banner(QLabel):
    """A coloured message line: info, ok, warn or error. Hidden when empty."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.hide()

    def show_message(self, text: str, level: str = "info") -> None:
        if not text:
            self.clear_message()
            return
        self.setProperty("level", level)
        self.setText(text)
        self.style().unpolish(self)
        self.style().polish(self)
        self.show()

    def clear_message(self) -> None:
        self.setText("")
        self.hide()


class LogPane(QWidget):
    """Collapsible live log of the underlying tool's output."""

    def __init__(self, label: str = "Log", collapsed: bool = True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setCheckable(True)
        self.toggle.setChecked(not collapsed)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow)
        self.toggle.setText(label)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.toggled.connect(self._toggled)
        head.addWidget(self.toggle)
        head.addStretch(1)
        self.show_cmd = QPushButton("Show Command")
        self.show_cmd.setToolTip("Copy the exact command lines this job ran")
        self.show_cmd.clicked.connect(self._show_commands)
        head.addWidget(self.show_cmd)
        lay.addLayout(head)
        self.text = QPlainTextEdit()
        self.text.setObjectName("Log")
        self.text.setReadOnly(True)
        self.text.setMaximumBlockCount(20000)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.text.setVisible(not collapsed)
        lay.addWidget(self.text)
        self.commands: list[str] = []

    def _toggled(self, on: bool) -> None:
        self.text.setVisible(on)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)

    def append(self, line: str) -> None:
        if line.startswith("$ "):
            self.commands.append(line[2:])
        self.text.appendPlainText(line)

    def clear(self) -> None:
        self.text.clear()
        self.commands = []

    def _show_commands(self) -> None:
        show_commands(self, self.commands)


def show_commands(parent, commands: list[str]) -> None:
    text = "\n".join(commands) if commands else "No commands have run yet."
    box = QMessageBox(parent)
    box.setWindowTitle("Commands")
    box.setText("The exact command lines, in order. Copied to the clipboard.")
    box.setDetailedText(text)
    box.setIcon(QMessageBox.Icon.Information)
    if commands:
        QGuiApplication.clipboard().setText(text)
    box.exec()


class DropZone(QFrame):
    files_dropped = pyqtSignal(list)

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self)
        self.label = QLabel(text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setWordWrap(True)
        self.label.setStyleSheet("background: transparent;")
        lay.addWidget(self.label)
        self.setMinimumHeight(110)

    def _hover(self, on: bool) -> None:
        self.setProperty("hover", on)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
            self._hover(True)

    def dragLeaveEvent(self, e):
        self._hover(False)

    def dropEvent(self, e):
        self._hover(False)
        paths = [Path(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        files = [p for p in paths if p.is_file()]
        if files:
            self.files_dropped.emit(files)


def human_size(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} TB"


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
