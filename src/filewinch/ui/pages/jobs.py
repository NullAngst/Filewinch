"""Jobs and logs: every job this session, its commands and full output."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QHBoxLayout, QListWidget, QListWidgetItem, QPlainTextEdit, QSplitter, QVBoxLayout,
                             QWidget)

from filewinch.ui.state import RUNNING
from filewinch.ui.widgets.common import button, hint, show_commands, title


class JobsPage(QWidget):
    name = "jobs"
    label = "Jobs and logs"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Jobs and logs"))
        lay.addWidget(hint("Everything FileWinch ran this session. Show Command copies the exact command "
                           "lines, to rerun in a terminal or attach to a bug report."))
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self._select)
        split.addWidget(self.list)
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        bar = QHBoxLayout()
        self.show_btn = button("Show Command")
        self.show_btn.clicked.connect(self._show)
        self.cancel_btn = button("Cancel Job")
        self.cancel_btn.clicked.connect(self._cancel)
        bar.addWidget(self.show_btn)
        bar.addWidget(self.cancel_btn)
        bar.addStretch(1)
        rv.addLayout(bar)
        self.text = QPlainTextEdit()
        self.text.setObjectName("Log")
        self.text.setReadOnly(True)
        rv.addWidget(self.text)
        split.addWidget(right)
        split.setSizes([260, 700])
        lay.addWidget(split, 1)
        state.jobs_changed.connect(self.refresh)
        state.job_line.connect(self._line)
        self.refresh()

    def _current(self):
        row = self.list.currentRow()
        return self.state.jobs[row] if 0 <= row < len(self.state.jobs) else None

    def refresh(self) -> None:
        keep = self._current()
        self.list.blockSignals(True)
        self.list.clear()
        for j in self.state.jobs:
            item = QListWidgetItem(f"{j.started:%H:%M:%S}  {j.name}\n{j.state}" +
                                   (f": {j.summary}" if j.summary else ""))
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.state.jobs:
            idx = self.state.jobs.index(keep) if keep in self.state.jobs else 0
            self.list.setCurrentRow(idx)
            self._select(idx)

    def _select(self, row: int) -> None:
        job = self._current()
        self.text.setPlainText("\n".join(job.log) if job else "")
        self.cancel_btn.setEnabled(bool(job and job.state == RUNNING))

    def _line(self, job_id: int, line: str) -> None:
        job = self._current()
        if job and job.id == job_id:
            self.text.appendPlainText(line)

    def _show(self) -> None:
        job = self._current()
        show_commands(self, job.commands if job else [])

    def _cancel(self) -> None:
        job = self._current()
        if job and job.cancel:
            job.cancel.cancel()
