"""Main window: sidebar on the left, one page at a time on the right."""

from __future__ import annotations

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
                             QStackedWidget, QVBoxLayout, QWidget)

from filewinch import __version__
from filewinch.ui import theme as theme_mod
from filewinch.ui.pages.dependencies import DependenciesPage
from filewinch.ui.pages.drives import DrivesPage
from filewinch.ui.pages.home import HomePage
from filewinch.ui.pages.jobs import JobsPage
from filewinch.ui.pages.partitions import PartitionsPage
from filewinch.ui.pages.repair import RepairPage
from filewinch.ui.pages.results import ResultsPage
from filewinch.ui.pages.scan import ScanPage
from filewinch.ui.pages.settings import SettingsPage
from filewinch.ui.state import AppState


class MainWindow(QMainWindow):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.setWindowTitle(f"FileWinch {__version__}")
        self.resize(1180, 780)
        self.setMinimumSize(900, 600)

        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        left = QVBoxLayout()
        left.setSpacing(0)
        app_title = QLabel("FileWinch")
        app_title.setObjectName("AppTitle")
        left.addWidget(app_title)
        self.sidebar = QListWidget()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(200)
        left.addWidget(self.sidebar, 1)
        lay.addLayout(left)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.pages = [
            HomePage(state), DrivesPage(state), ScanPage(state), ResultsPage(state), RepairPage(state),
            PartitionsPage(state), JobsPage(state), DependenciesPage(state), SettingsPage(state, self.apply_theme),
        ]
        for p in self.pages:
            self.stack.addWidget(p)
            QListWidgetItem(p.label, self.sidebar)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        state.navigate.connect(self.go)
        start = "dependencies" if state.report.missing_required() else "home"
        self.go(start)

    def go(self, name: str) -> None:
        for i, p in enumerate(self.pages):
            if p.name == name:
                self.sidebar.setCurrentRow(i)
                return

    def current_page_name(self) -> str:
        return self.pages[self.stack.currentIndex()].name

    def apply_theme(self) -> None:
        s = self.state.settings
        theme_mod.apply(QApplication.instance(), s.theme, s.theme_follows_system)
        for p in self.pages:
            if hasattr(p, "refresh") and p.name == "dependencies":
                p.refresh()

    def closeEvent(self, e):
        busy = [j for j in self.state.jobs if j.state == "running"]
        if busy:
            from PyQt6.QtWidgets import QMessageBox
            if QMessageBox.question(
                    self, "Jobs still running",
                    "Some jobs are still running. Quit anyway? Imaging and carving resume from where they "
                    "stopped next time you start the same recovery.") != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            for j in busy:
                if j.cancel:
                    j.cancel.cancel()
        super().closeEvent(e)


_APP = None  # held here so it is never garbage-collected


def _app(argv=None) -> QApplication:
    global _APP
    app = QApplication.instance() or QApplication(argv or ["filewinch"])
    _APP = app
    app.setApplicationName("FileWinch")
    app.setOrganizationName("FileWinch")
    app.setDesktopFileName("filewinch")
    icon = theme_mod.THEME_DIR.parent / "filewinch.svg"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))
    return app


def build_for_test() -> MainWindow:
    """Build the window without showing it or starting the event loop."""
    _app()
    state = AppState()
    w = MainWindow(state)
    w.apply_theme()
    return w


def main(argv=None) -> int:
    import sys
    app = _app(argv or sys.argv)
    state = AppState()
    w = MainWindow(state)
    w.apply_theme()
    w.show()
    return app.exec()


__all__ = ["MainWindow", "main", "build_for_test"]
