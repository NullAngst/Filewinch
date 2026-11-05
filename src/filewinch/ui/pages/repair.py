"""Repair: drop files, see what is wrong, watch the recipe chain, open the output."""

from __future__ import annotations

import threading
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QFileDialog, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                             QMessageBox, QSplitter, QStyle, QVBoxLayout, QWidget)

from filewinch.repair import engine
from filewinch.repair.common import size_text
from filewinch.ui.state import run_in_thread
from filewinch.ui.widgets.common import Banner, DropZone, LogPane, button, hint, title

ROLE = Qt.ItemDataRole.UserRole


class RepairPage(QWidget):
    name = "repair"
    label = "Repair"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.queue: list[Path] = []
        self.outcomes: dict[Path, engine.Outcome] = {}
        self.running = False
        self.current: Path | None = None
        self.job = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Repair a file"))
        lay.addWidget(hint("The original is never touched. Each fix is written as <name>.repaired.<ext> with a "
                           "report beside it. Lossless fixes are tried first; a re-encode only runs if you say so."))
        self.banner = Banner()
        lay.addWidget(self.banner)

        top = QHBoxLayout()
        self.drop = DropZone("Drop damaged files here")
        self.drop.files_dropped.connect(self.add_files)
        top.addWidget(self.drop, 2)
        side = QVBoxLayout()
        add = button("Add Files")
        add.clicked.connect(self._pick_files)
        side.addWidget(add)
        ref_box = QGroupBox("Reference file for MP4 and MOV")
        rl = QVBoxLayout(ref_box)
        rrow = QHBoxLayout()
        self.ref_edit = QLineEdit()
        self.ref_edit.setPlaceholderText("optional")
        rpick = button("Choose")
        rpick.clicked.connect(self._pick_ref)
        rrow.addWidget(self.ref_edit, 1)
        rrow.addWidget(rpick)
        rl.addLayout(rrow)
        why = hint("A video whose index (moov) is gone can only be rebuilt against a good file from the "
                   "same camera or app, recorded with the same settings. untrunc learns the layout from it.")
        rl.addWidget(why)
        side.addWidget(ref_box)
        orow = QHBoxLayout()
        orow.addWidget(QLabel("Save to"))
        self.out_edit = QLineEdit(str(Path(state.settings.output_dir) / "repaired"))
        opick = button("Choose")
        opick.clicked.connect(self._pick_out)
        orow.addWidget(self.out_edit, 1)
        orow.addWidget(opick)
        side.addLayout(orow)
        top.addLayout(side, 3)
        lay.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.files = QListWidget()
        self.files.currentItemChanged.connect(self._select)
        split.addWidget(self._boxed("Queue", self.files))
        self.findings = QListWidget()
        self.findings.setWordWrap(True)
        split.addWidget(self._boxed("What is wrong", self.findings))
        self.chain = QListWidget()
        self.chain.setWordWrap(True)
        split.addWidget(self._boxed("Recipe chain", self.chain))
        split.setSizes([220, 380, 380])
        lay.addWidget(split, 1)

        bar = QHBoxLayout()
        self.start_btn = button("Repair", primary=True)
        self.start_btn.clicked.connect(self.start)
        self.stop_btn = button("Stop")
        self.stop_btn.clicked.connect(self.stop)
        self.clear_btn = button("Clear Finished")
        self.clear_btn.clicked.connect(self._clear)
        self.open_btn = button("Open Output")
        self.open_btn.clicked.connect(self._open_output)
        self.report_btn = button("Open Report")
        self.report_btn.clicked.connect(self._open_report)
        for b in (self.start_btn, self.stop_btn, self.clear_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        bar.addWidget(self.report_btn)
        bar.addWidget(self.open_btn)
        lay.addLayout(bar)
        self.log = LogPane("Live log")
        lay.addWidget(self.log)
        state.repair_queue.connect(self.add_files)
        self._update()

    def _boxed(self, label: str, w: QWidget) -> QGroupBox:
        g = QGroupBox(label)
        v = QVBoxLayout(g)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(w)
        return g

    def _icon(self, which: str):
        sp = {"ok": QStyle.StandardPixmap.SP_DialogApplyButton, "fail": QStyle.StandardPixmap.SP_DialogCancelButton,
              "run": QStyle.StandardPixmap.SP_BrowserReload, "skip": QStyle.StandardPixmap.SP_MediaSkipForward,
              "wait": QStyle.StandardPixmap.SP_FileIcon}[which]
        return self.style().standardIcon(sp)

    # --- queue -------------------------------------------------------------------

    def _pick_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Files to repair", str(Path.home()))
        self.add_files([Path(p) for p in paths])

    def _pick_ref(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, "A good file from the same camera or app", str(Path.home()),
                                           "Video (*.mp4 *.mov *.m4v *.3gp);;All files (*)")
        if p:
            self.ref_edit.setText(p)

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save repaired files to", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def add_files(self, paths: list) -> None:
        for p in paths:
            p = Path(p)
            if p in self.queue or not p.is_file():
                continue
            self.queue.append(p)
            item = QListWidgetItem(self._icon("wait"), p.name)
            item.setToolTip(str(p))
            item.setData(ROLE, p)
            self.files.addItem(item)
        if self.files.currentRow() < 0 and self.files.count():
            self.files.setCurrentRow(0)
        self._update()

    def _item_for(self, p: Path) -> QListWidgetItem | None:
        for i in range(self.files.count()):
            if self.files.item(i).data(ROLE) == p:
                return self.files.item(i)
        return None

    def _clear(self) -> None:
        for p in list(self.outcomes):
            it = self._item_for(p)
            if it:
                self.files.takeItem(self.files.row(it))
            self.queue.remove(p)
            del self.outcomes[p]
        self._update()

    def _update(self) -> None:
        pending = [p for p in self.queue if p not in self.outcomes]
        self.start_btn.setEnabled(bool(pending) and not self.running)
        self.stop_btn.setEnabled(self.running)
        o = self._selected_outcome()
        self.open_btn.setEnabled(bool(o and o.output))
        self.report_btn.setEnabled(bool(o and o.report))

    def _selected_outcome(self) -> engine.Outcome | None:
        it = self.files.currentItem()
        return self.outcomes.get(it.data(ROLE)) if it else None

    def _select(self, item, _prev=None) -> None:
        self.findings.clear()
        self.chain.clear()
        o = self._selected_outcome()
        if o:
            self._show_findings(o.findings)
            for a in o.attempts:
                self._show_attempt(a)
            self.banner.show_message(o.message + (f" Output: {o.output.name}" if o.output else ""),
                                     "ok" if o.fully_verified else "warn" if o.output else "error")
        self._update()

    # --- running -------------------------------------------------------------------

    def start(self) -> None:
        if self.running:
            return
        reason = self.state.missing_reason("ffmpeg", "ffprobe", "file", "fidentify")
        if reason:
            self.banner.show_message(reason, "error")
            return
        self.running = True
        self.job = self.state.new_job("Repair")
        self.log.clear()
        self._update()
        self._next()

    def stop(self) -> None:
        if self.job and self.job.cancel:
            self.job.cancel.cancel()

    def _next(self) -> None:
        pending = [p for p in self.queue if p not in self.outcomes]
        if not pending or not self.running or (self.job and self.job.cancel.cancelled):
            self.running = False
            done = sum(1 for o in self.outcomes.values() if o.fully_verified)
            self.state.finish_job(self.job, "done", f"{done} of {len(self.outcomes)} fully repaired")
            self._update()
            return
        src = pending[0]
        self.current = src
        it = self._item_for(src)
        it.setIcon(self._icon("run"))
        self.files.setCurrentItem(it)
        self.findings.clear()
        self.chain.clear()
        self.banner.show_message(f"Working on {src.name}...", "info")
        out_dir = Path(self.out_edit.text()).expanduser()
        ref = Path(self.ref_edit.text()) if self.ref_edit.text().strip() else None
        settings = self.state.settings
        tools = self.state.report
        cancel = self.job.cancel

        def run(bridge):
            def approve(estimates):
                answer = {}
                ev = threading.Event()
                bridge.event.emit("approve", (estimates, answer, ev))
                ev.wait()
                return answer.get("choice")

            return engine.repair(src, out_dir, tools, lossy_codec=settings.lossy_codec, reference=ref,
                                 approve=approve, log=bridge.line.emit, cancel=cancel,
                                 on_event=lambda k, p: bridge.event.emit(k, p))

        bridge = run_in_thread(run)
        bridge.line.connect(self._log)
        bridge.event.connect(self._event)
        bridge.finished.connect(lambda o: self._done(src, o))
        bridge.failed.connect(lambda e: self._failed(src, e))
        bridge.start()
        self._bridge = bridge

    def _log(self, line: str) -> None:
        self.log.append(line)
        if self.job:
            self.state.job_log(self.job, line)

    def _event(self, kind: str, payload) -> None:
        if kind == "findings":
            self.findings.clear()
            self._show_findings(payload)
        elif kind == "attempt":
            self._show_attempt(payload)
        elif kind == "approve":
            estimates, answer, ev = payload
            answer["choice"] = self._ask_reencode(estimates)
            ev.set()

    def _show_findings(self, findings) -> None:
        if not findings:
            self.findings.addItem("No problems found by the checks.")
        for f in findings:
            item = QListWidgetItem(f.text)
            if f.evidence:
                item.setToolTip(f.evidence)
            self.findings.addItem(item)

    def _show_attempt(self, a) -> None:
        for i in range(self.chain.count()):
            if self.chain.item(i).data(ROLE) is a:
                item = self.chain.item(i)
                break
        else:
            item = QListWidgetItem()
            item.setData(ROLE, a)
            self.chain.addItem(item)
        if not a.ran:
            item.setIcon(self._icon("skip"))
            item.setText(f"{a.recipe}\n  skipped: {a.skipped}")
        elif a.verify is None:
            item.setIcon(self._icon("run"))
            item.setText(f"{a.recipe}\n  running...")
        else:
            item.setIcon(self._icon("ok" if a.verify.ok else "fail"))
            item.setText(f"{a.recipe}\n  {a.verify.detail}")
        item.setToolTip("\n".join(a.commands))

    def _ask_reencode(self, est: dict) -> str | None:
        codec = "H.265" if self.state.settings.lossy_codec == "h265" else "H.264"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Re-encode?")
        box.setText("No lossless fix verified. A re-encode decodes every frame ffmpeg can, patches damaged "
                    "frames from their neighbours, and writes a new video.\n\n"
                    f"Lossless FFV1: every pixel kept, about {size_text(est['ffv1'])}.\n"
                    f"Lossy {codec}: watchable at a sane size, about {size_text(est['lossy'])}.\n\n"
                    "Sizes are rough estimates.")
        ffv1 = box.addButton("Lossless FFV1", QMessageBox.ButtonRole.AcceptRole)
        lossy = box.addButton(f"Lossy {codec}", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        c = box.clickedButton()
        return "ffv1" if c is ffv1 else "lossy" if c is lossy else None

    def _done(self, src: Path, o: engine.Outcome) -> None:
        self.outcomes[src] = o
        it = self._item_for(src)
        if it:
            it.setIcon(self._icon("ok" if o.fully_verified else "fail"))
        self.banner.show_message(o.message + (f" Output: {o.output.name}" if o.output else ""),
                                 "ok" if o.fully_verified else "warn" if o.output else "error")
        if not o.original_untouched:
            self.banner.show_message("The original's hash changed during repair. Stop and check the drive.",
                                     "error")
        self._next()

    def _failed(self, src: Path, err: str) -> None:
        o = engine.Outcome(source=src, message=f"Repair crashed: {err}")
        self.outcomes[src] = o
        it = self._item_for(src)
        if it:
            it.setIcon(self._icon("fail"))
        self.banner.show_message(o.message, "error")
        self._next()

    def _open_output(self) -> None:
        o = self._selected_outcome()
        if o and o.output:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(o.output)))

    def _open_report(self) -> None:
        o = self._selected_outcome()
        if o and o.report:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(o.report)))

