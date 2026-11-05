"""Drives: pick what to recover from."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QFileDialog, QHBoxLayout, QHeaderView, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from filewinch.backends import smart
from filewinch.core import devices as devmod
from filewinch.ui import theme as theme_mod
from filewinch.ui.state import run_in_thread
from filewinch.ui.widgets.common import Banner, button, hint, human_size, title

COLS = ["Device", "Model", "Size", "Filesystem", "Mounted", "Health", "Notes"]


class DrivesPage(QWidget):
    name = "drives"
    label = "Drives"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.devices: list[devmod.Device] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Pick what to recover from"))
        lay.addWidget(hint("Removable and optical drives are listed first. Health needs root, so it is "
                           "checked when you ask or when a scan starts. Got a disk image already? Open it "
                           "directly."))
        self.banner = Banner()
        lay.addWidget(self.banner)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(COLS)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemDoubleClicked.connect(lambda *_: self._recover())
        h = self.tree.header()
        for i in range(len(COLS) - 1):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(len(COLS) - 1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.tree, 1)
        bar = QHBoxLayout()
        self.refresh_btn = button("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.health_btn = button("Check Health")
        self.health_btn.clicked.connect(self._check_health)
        self.image_btn = button("Open Disk Image")
        self.image_btn.clicked.connect(self._open_image)
        self.go_btn = button("Recover From Selected", primary=True)
        self.go_btn.clicked.connect(self._recover)
        for b in (self.refresh_btn, self.health_btn, self.image_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.go_btn)
        lay.addLayout(bar)
        self._selected()
        self._loaded = False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._loaded:
            self._loaded = True
            self.refresh()

    def refresh(self) -> None:
        reason = self.state.missing_reason("lsblk")
        if reason:
            self.banner.show_message(reason, "error")
            return
        self.devices = devmod.list_devices()
        self._fill()

    def _row(self, d: devmod.Device) -> list[str]:
        notes = []
        if d.kind == "rom":
            notes.append(d.disc or "no disc")
        if d.trim_warning:
            notes.append("SSD with TRIM: deleted files are usually gone for good")
        elif d.is_ssd and d.kind == "disk":
            notes.append("SSD")
        if d.removable and d.kind != "rom":
            notes.append("removable")
        health = d.smart.text() if d.smart else ("" if d.kind == "rom" else "not checked")
        return [d.path, d.model or d.label, human_size(d.size) if d.size else "", d.fstype,
                ", ".join(d.all_mountpoints()) or "no", health, "; ".join(notes)]

    def _fill(self) -> None:
        th = theme_mod.resolve(self.state.settings.theme, self.state.settings.theme_follows_system)
        self.tree.clear()

        def add(parent, d):
            item = QTreeWidgetItem(self._row(d))
            item.setData(0, Qt.ItemDataRole.UserRole, d)
            if d.trim_warning:
                item.setForeground(6, theme_mod.color(th, "warn"))
            if d.smart and d.smart.verdict in (smart.WARN, smart.FAILING):
                item.setForeground(5, theme_mod.color(th, "bad" if d.smart.verdict == smart.FAILING else "warn"))
            if parent is None:
                self.tree.addTopLevelItem(item)
            else:
                parent.addChild(item)
            for c in d.children:
                add(item, c)
            return item

        for d in self.devices:
            add(None, d)
        self.tree.expandAll()
        if not self.devices:
            self.banner.show_message("No drives found. Open a disk image instead, or plug the drive in and "
                                     "hit Refresh.", "info")
        else:
            self.banner.clear_message()

    def _current(self) -> devmod.Device | None:
        items = self.tree.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    def _selected(self) -> None:
        d = self._current()
        self.go_btn.setEnabled(d is not None)
        self.health_btn.setEnabled(d is not None and d.kind in ("disk",) and not self.state.missing_reason("smartctl"))
        if d and d.trim_warning:
            self.banner.show_message(
                f"{d.path} is an SSD with discard (TRIM) enabled. Once the drive discards deleted blocks "
                "they read back as zeros: undelete may list names, and carving will find nothing. "
                "FileWinch will still try.", "warn")
        elif d is not None:
            self.banner.clear_message()

    def _check_health(self) -> None:
        d = self._current()
        if not d:
            return
        job = self.state.new_job(f"Health check {d.path}")
        bridge = run_in_thread(lambda bridge: smart.check(d.path, log=bridge.line.emit))
        bridge.line.connect(lambda l: self.state.job_log(job, l))

        def done(h):
            d.smart = h
            self.state.finish_job(job, "done", h.text())
            self._fill()

        bridge.finished.connect(done)
        bridge.failed.connect(lambda e: self.state.finish_job(job, "failed", e))
        bridge.start()

    def _open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open a disk image", str(Path.home()),
                                              "Disk images (*.img *.dd *.iso *.raw *.bin *.e01);;All files (*)")
        if path:
            self.state.source_chosen.emit(Path(path))
            self.state.navigate.emit("scan")

    def _recover(self) -> None:
        d = self._current()
        if d:
            self.state.source_chosen.emit(d)
            self.state.navigate.emit("scan")
