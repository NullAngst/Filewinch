"""Dependencies: what is needed, what is found, how to install the rest."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QTableWidget, QTableWidgetItem,
                             QVBoxLayout, QWidget)

from filewinch.core import deps
from filewinch.ui import theme as theme_mod
from filewinch.ui.widgets.common import Banner, button, hint, title

COLS = ["Tool", "Status", "Version", "Needed", "Feature", "Install"]


class DependenciesPage(QWidget):
    name = "dependencies"
    label = "Dependencies"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Dependencies"))
        lay.addWidget(hint("FileWinch runs your system's own tools. Missing required tools block jobs; "
                           "missing optional ones grey out one feature. Amber means older than a version "
                           "with a known security fix; your distro may have backported it, so it is a "
                           "warning, nothing is blocked."))
        top = QHBoxLayout()
        self.copy_btn = button("Copy Install Command", primary=True)
        self.copy_btn.clicked.connect(self.copy_all)
        self.recheck_btn = button("Recheck")
        self.recheck_btn.clicked.connect(state.recheck)
        top.addWidget(self.copy_btn)
        top.addWidget(self.recheck_btn)
        top.addStretch(1)
        lay.addLayout(top)
        self.banner = Banner()
        lay.addWidget(self.banner)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        h = self.table.horizontalHeader()
        for i in range(4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(4, 230)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        state.deps_changed.connect(self.refresh)
        state.settings_changed.connect(self.refresh)
        self.refresh()

    def _status(self, t: deps.Tool, rep: deps.Report) -> tuple[str, str]:
        if t.status == deps.FOUND:
            return "found", "good"
        if t.status == deps.OUTDATED:
            return "outdated", "warn"
        if t.status == deps.LIMITED:
            return "limited", "warn"
        if t.group and rep.group_satisfied(t.group):
            other = next((m.key for m in rep.tools.values() if m.group == t.group and m.path), "")
            return f"not needed, {other} found", "muted"
        return "missing", "bad" if t.required else "muted"

    def refresh(self) -> None:
        rep = self.state.report
        th = theme_mod.resolve(self.state.settings.theme, self.state.settings.theme_follows_system)
        rows = sorted(rep.tools.values(), key=lambda t: (not t.required, t.group, t.key))
        self.table.setRowCount(len(rows))
        for r, t in enumerate(rows):
            status, colour = self._status(t, rep)
            detail = t.detail
            if t.status == deps.LIMITED and t.limited_note:
                detail = f"{t.detail}. {t.limited_note}"
            needed = "required" if t.required else "optional"
            if t.group == "privilege":
                needed = "required, one of pkexec or sudo"
            elif t.group == "terminal":
                needed = "one terminal, partition repair only"
            if t.bundled:
                needed = "bundled"
            install = ""
            if t.status == deps.MISSING and not (t.group and rep.group_satisfied(t.group)):
                install = rep.install_command_for(t.key)
            elif t.status in (deps.OUTDATED, deps.LIMITED):
                install = detail
            cells = [t.key, status, t.version or "", needed, t.feature, install]
            for c, val in enumerate(cells):
                item = QTableWidgetItem(val)
                item.setToolTip(t.path or val)
                if c == 1:
                    item.setForeground(theme_mod.color(th, colour))
                if c == 5 and val:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(r, c, item)
        self.table.resizeRowsToContents()
        missing = rep.missing_required()
        if missing:
            self.banner.show_message("Missing required tools: " + ", ".join(t.key for t in missing) +
                                     ". Copy Install Command builds one command for everything missing.", "error")
        elif rep.ffmpeg_gaps:
            note = rep.tools["ffmpeg"].limited_note
            self.banner.show_message("ffmpeg is limited: " + ", ".join(rep.ffmpeg_gaps) + ". " + note, "warn")
        else:
            pm = rep.package_manager or "no known package manager"
            self.banner.show_message(f"Every required tool is here. Package manager: {pm}.", "ok")
        self.copy_btn.setEnabled(bool(rep.everything_missing_command()))

    def copy_all(self) -> None:
        cmd = self.state.report.everything_missing_command()
        if cmd:
            QGuiApplication.clipboard().setText(cmd)
            self.banner.show_message("Copied:\n" + cmd, "info")
