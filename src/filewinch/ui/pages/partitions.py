"""Partitions: back up the table, open TestDisk, show what changed.

TestDisk is driven by you. FileWinch only does the safety around it.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QVBoxLayout,
                             QWidget)

from filewinch.backends import testdisk
from filewinch.core import devices as devmod
from filewinch.core import safety
from filewinch.core.jobs import ProcessJob
from filewinch.ui.widgets.common import Banner, button, hint, human_size, title


class PartitionsPage(QWidget):
    name = "partitions"
    label = "Partitions"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.devices: list[devmod.Device] = []
        self.backup: Path | None = None
        self.proc: ProcessJob | None = None
        self.job = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Fix a partition table"))
        lay.addWidget(hint("For a drive that shows up but whose partitions are gone. FileWinch backs up the "
                           "current table, then opens TestDisk in a terminal for you to drive. Nothing is written "
                           "until you tell TestDisk to write. Afterwards you see exactly what changed."))
        self.banner = Banner()
        lay.addWidget(self.banner)

        row = QHBoxLayout()
        row.addWidget(QLabel("Drive"))
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._changed)
        row.addWidget(self.combo, 1)
        refresh = button("Refresh")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        lay.addLayout(row)

        steps = QHBoxLayout()
        self.backup_btn = button("1. Back Up Table", primary=True)
        self.backup_btn.clicked.connect(self._backup)
        self.testdisk_btn = button("2. Open TestDisk", primary=True)
        self.testdisk_btn.clicked.connect(self._open_testdisk)
        self.copy_btn = button("Copy Restore Command")
        self.copy_btn.clicked.connect(self._copy_restore)
        for b in (self.backup_btn, self.testdisk_btn):
            steps.addWidget(b)
        steps.addStretch(1)
        steps.addWidget(self.copy_btn)
        lay.addLayout(steps)

        cur = QGroupBox("Current table (the backup)")
        cv = QVBoxLayout(cur)
        self.where = hint("")
        cv.addWidget(self.where)
        self.table_text = QPlainTextEdit()
        self.table_text.setObjectName("Log")
        self.table_text.setReadOnly(True)
        cv.addWidget(self.table_text)
        lay.addWidget(cur, 1)

        diff = QGroupBox("What TestDisk changed")
        dv = QVBoxLayout(diff)
        self.diff_text = QPlainTextEdit()
        self.diff_text.setObjectName("Log")
        self.diff_text.setReadOnly(True)
        dv.addWidget(self.diff_text)
        lay.addWidget(diff, 1)
        self._loaded = False
        self._update()

    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def refresh(self) -> None:
        self.devices = [d for d in devmod.list_devices() if d.kind == "disk"]
        self.combo.blockSignals(True)
        self.combo.clear()
        for d in self.devices:
            mounted = " (mounted)" if d.mounted else ""
            self.combo.addItem(f"{d.path}  {d.model}  {human_size(d.size)}{mounted}", d.path)
        self.combo.blockSignals(False)
        self._changed()

    def _device(self) -> devmod.Device | None:
        i = self.combo.currentIndex()
        return self.devices[i] if 0 <= i < len(self.devices) else None

    def _changed(self) -> None:
        self.backup = None
        self.table_text.clear()
        self.diff_text.clear()
        self.where.setText("")
        self.banner.clear_message()
        d = self._device()
        if d and d.mounted:
            self.banner.show_message("A partition on this drive is mounted at " + ", ".join(d.all_mountpoints()) +
                                     ". Partition repair will not start until it is unmounted.", "warn")
        self._update()

    def _update(self) -> None:
        d = self._device()
        reason = self.state.missing_reason("sfdisk")
        self.backup_btn.setEnabled(d is not None and not reason and self.proc is None)
        self.backup_btn.setToolTip(reason)
        td_reason = self.state.missing_reason("testdisk")
        if not self.state.report.group_satisfied("terminal"):
            td_reason = (td_reason + "\n" if td_reason else "") + "Needs a terminal: " + \
                self.state.report.install_command_for("konsole")
        ok = d is not None and self.backup is not None and not d.mounted and not td_reason and self.proc is None
        self.testdisk_btn.setEnabled(ok)
        self.testdisk_btn.setToolTip(td_reason or ("" if self.backup else "Back up the table first"))
        self.copy_btn.setEnabled(self.backup is not None)

    def _backup(self) -> None:
        d = self._device()
        if not d:
            return
        dest = safety.backup_path(d.model or d.name, d.serial or "noserial")
        job = self.state.new_job(f"Back up partition table of {d.path}")
        try:
            safety.dump_partition_table(d.path, dest, log=lambda l: self.state.job_log(job, l))
        except Exception as exc:  # SafetyError or PrivilegeError, both shown as-is
            self.backup = None
            self.state.finish_job(job, "failed", str(exc))
            self.banner.show_message(str(exc), "error")
            self._update()
            return
        self.backup = dest
        self.state.finish_job(job, "done", str(dest))
        self.table_text.setPlainText(dest.read_text())
        self.where.setText(f"Saved to {dest}\nTo put it back: {safety.restore_command(d.path, dest)}")
        self.banner.show_message("Backup saved. If anything goes wrong, the restore command above puts this "
                                 "exact table back.", "ok")
        self._update()

    def _copy_restore(self) -> None:
        d = self._device()
        if d and self.backup:
            QGuiApplication.clipboard().setText(safety.restore_command(d.path, self.backup))

    def _open_testdisk(self) -> None:
        d = self._device()
        if not d:
            return
        # Recheck mounts now: something may have mounted it since the backup.
        fresh = devmod.find(devmod.list_devices(), d.path) or d
        try:
            safety.partition_repair_preflight(fresh, self.backup)
        except safety.SafetyError as exc:
            self.banner.show_message(str(exc), "error")
            return
        if QMessageBox.warning(
                self, "Open TestDisk",
                f"TestDisk will open on {d.path} ({d.model}, {human_size(d.size)}) as root.\n\n"
                "Check that is the right drive. TestDisk only writes when you choose Write in its menus.",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Ok:
            return
        log_dir = Path.home() / ".local" / "share" / "filewinch" / "testdisk-logs" / \
            f"{d.name}-{_dt.datetime.now():%Y%m%d-%H%M%S}"
        try:
            argv = testdisk.partition_repair_argv(d.path, log_dir)
        except Exception as exc:
            self.banner.show_message(str(exc), "error")
            return
        self.log_dir = log_dir
        self.job = self.state.new_job(f"TestDisk on {d.path}")
        self.proc = ProcessJob(argv, parent=self)
        self.proc.line.connect(lambda l: self.state.job_log(self.job, l))
        self.proc.finished.connect(self._testdisk_done)
        self.banner.show_message("TestDisk is open in a terminal. Come back here when you have closed it.", "info")
        self._update()
        self.proc.start()

    def _testdisk_done(self, code: int) -> None:
        self.proc = None
        d = self._device()
        after = Path(str(self.backup) + ".after")
        try:
            safety.dump_partition_table(d.path, after, log=lambda l: self.state.job_log(self.job, l))
            diff = safety.diff_tables(self.backup.read_text(), after.read_text())
            after_text = after.read_text()
            after.unlink()
        except Exception as exc:
            diff = [f"Could not read the table after TestDisk: {exc}"]
            after_text = ""
        if diff:
            self.diff_text.setPlainText("\n".join(diff))
            summary = "table changed"
            self.banner.show_message("TestDisk changed the partition table. The diff is below. If it is wrong: " +
                                     safety.restore_command(d.path, self.backup), "warn")
        else:
            self.diff_text.setPlainText("No change. The partition table is the same as the backup.\n\n" + after_text)
            summary = "no change"
            self.banner.show_message("TestDisk closed without changing the partition table.", "info")
        logs = testdisk.logs(self.log_dir)
        if logs:
            self.state.job_log(self.job, logs)
        self.state.finish_job(self.job, "done", summary)
        self._update()
