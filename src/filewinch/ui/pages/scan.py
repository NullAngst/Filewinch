"""Scan: the undelete pipeline, one progress bar per stage.

Stages, in order, depending on the source:
  disk or partition:  Health, Image (or Direct), Find deleted names, Carve
  disk image file:    Find deleted names, Carve
  data CD/DVD/BD:     Image (2048-byte sectors), Find names, then Carve, or
                      pull the VOB/VRO/M2TS files and send them to Repair
  audio CD:           Rip with cdparanoia
  Video CD / SVCD:    Rip with vcdxrip, send the MPEG files to Repair

Safety checks run before anything starts (core/safety.py).
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QButtonGroup, QComboBox, QFileDialog, QFormLayout, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar, QRadioButton,
                             QVBoxLayout, QWidget)

from filewinch.backends import cdparanoia, ddrescue, photorec, smart, vcd
from filewinch.core import devices as devmod
from filewinch.core import privilege, safety, undelete
from filewinch.core.jobs import ProcessJob, run_command
from filewinch.ui.state import CANCELLED, DONE, FAILED, run_in_thread
from filewinch.ui.widgets.common import Banner, LogPane, button, hint, hms, human_size, title

S_HEALTH = "Health check"
S_IMAGE = "Image the source"
S_NAMED = "Find deleted files by name"
S_CARVE = "Carve for lost files"
S_PULL = "Pull video files off the disc"
S_AUDIO = "Rip audio tracks"
S_VCD = "Rip Video CD tracks"


@dataclass
class ScanResult:
    source: str                     # what the later stages read: image file or device
    root: bool                      # Sleuth Kit reads need root (direct device scan)
    job_dir: Path
    named: list = field(default_factory=list)
    carved: list = field(default_factory=list)
    fstypes: dict = field(default_factory=dict)
    note: str = ""


class StageRow:
    def __init__(self, grid: QGridLayout, row: int, name: str):
        self.name = name
        self.label = QLabel(name)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setFormat("waiting")
        self.time = QLabel("")
        self.time.setObjectName("Hint")
        grid.addWidget(self.label, row, 0)
        grid.addWidget(self.bar, row, 1)
        grid.addWidget(self.time, row, 2)
        self.started = 0.0
        self.frac = 0.0
        self.state = "waiting"

    def start(self, indeterminate: bool = False) -> None:
        self.started = time.monotonic()
        self.state = "running"
        if indeterminate:
            self.bar.setRange(0, 0)
        self.bar.setFormat("%p%")

    def set(self, frac: float, text: str = "") -> None:
        self.frac = frac
        if self.bar.maximum() == 0:
            self.bar.setRange(0, 1000)
        self.bar.setValue(int(frac * 1000))
        if text:
            self.bar.setFormat(text)

    def note(self, text: str) -> None:
        if self.bar.maximum() != 0:
            self.bar.setFormat(text)
        else:
            self.time.setText(text)

    def tick(self) -> None:
        if self.state != "running":
            return
        el = time.monotonic() - self.started
        txt = f"{hms(el)} elapsed"
        if 0.01 < self.frac < 1.0:
            txt += f", about {hms(el * (1 - self.frac) / self.frac)} left"
        self.time.setText(txt)

    def finish(self, state: str, text: str = "") -> None:
        self.state = state
        self.bar.setRange(0, 1000)
        self.bar.setValue(1000 if state == DONE else self.bar.value())
        self.bar.setFormat(text or {DONE: "done", FAILED: "failed", CANCELLED: "stopped", "skipped": "skipped"}
                           .get(state, state))
        if self.started:
            self.time.setText(hms(time.monotonic() - self.started))


class ScanPage(QWidget):
    name = "scan"
    label = "Scan"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.source = None               # Device or Path
        self.health = None
        self.rows: dict[str, StageRow] = {}
        self.plan: list[str] = []
        self.running = False
        self.job = None
        self.proc: ProcessJob | None = None
        self.result: ScanResult | None = None

        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Scan"))
        self.source_label = hint("No source picked yet. Go to Drives and pick one, or open a disk image.")
        lay.addWidget(self.source_label)
        self.banner = Banner()
        lay.addWidget(self.banner)

        opts = QGroupBox("Options")
        f = QFormLayout(opts)
        row = QHBoxLayout()
        self.out_edit = QLineEdit(state.settings.output_dir)
        pick = button("Choose")
        pick.clicked.connect(self._pick_out)
        row.addWidget(self.out_edit, 1)
        row.addWidget(pick)
        f.addRow("Save to", row)
        self.r_image = QRadioButton("Image it first, then work on the image (recommended)")
        self.r_direct = QRadioButton("Scan the drive directly (every extra read wears a failing drive)")
        self.r_image.setChecked(True)
        grp = QButtonGroup(self)
        grp.addButton(self.r_image)
        grp.addButton(self.r_direct)
        self.modes = QWidget()
        modes = QVBoxLayout(self.modes)
        modes.setContentsMargins(0, 0, 0, 0)
        modes.addWidget(self.r_image)
        modes.addWidget(self.r_direct)
        f.addRow("How", self.modes)
        self.form = f
        self.carve_mode = QComboBox()
        self.carve_mode.addItem("Automatic: free space when the filesystem is readable, else everything", "auto")
        self.carve_mode.addItem("Whole source", photorec.WHOLE)
        self.carve_mode.addItem("Do not carve, names only", "off")
        f.addRow("Carving", self.carve_mode)
        lay.addWidget(opts)
        self.opts = opts

        stages = QGroupBox("Progress")
        self.grid = QGridLayout(stages)
        self.grid.setColumnStretch(1, 1)
        lay.addWidget(stages)

        bar = QHBoxLayout()
        self.start_btn = button("Start", primary=True)
        self.start_btn.clicked.connect(self.start)
        self.cancel_btn = button("Stop")
        self.cancel_btn.clicked.connect(self.cancel)
        self.cancel_btn.setEnabled(False)
        self.results_btn = button("Open Results")
        self.results_btn.clicked.connect(lambda: state.navigate.emit("results"))
        self.results_btn.setEnabled(False)
        bar.addWidget(self.start_btn)
        bar.addWidget(self.cancel_btn)
        bar.addStretch(1)
        bar.addWidget(self.results_btn)
        lay.addLayout(bar)

        self.log = LogPane("Live log")
        lay.addWidget(self.log, 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(1000)
        state.source_chosen.connect(self.set_source)
        self._update_controls()

    # --- setup -----------------------------------------------------------------

    def _pick_out(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Save recovered files to", self.out_edit.text())
        if d:
            self.out_edit.setText(d)

    def set_source(self, src) -> None:
        if self.running:
            return
        self.source = src
        self.health = getattr(src, "smart", None)
        self.result = None
        self.results_btn.setEnabled(False)
        self.banner.clear_message()
        if isinstance(src, Path):
            self.source_label.setText(f"Source: disk image {src} ({human_size(src.stat().st_size)})")
        else:
            bits = [src.path, src.model, human_size(src.size)]
            if src.kind == "rom":
                bits.append(src.disc or "no disc")
            self.source_label.setText("Source: " + ", ".join(b for b in bits if b))
            if src.trim_warning:
                self.banner.show_message("SSD with TRIM enabled: deleted blocks probably read back as zeros. "
                                         "Names may be listed, carving will likely find nothing.", "warn")
        self._plan()
        self._update_controls()

    def _is_disc(self) -> bool:
        return isinstance(self.source, devmod.Device) and self.source.kind == "rom"

    def _plan(self) -> None:
        src = self.source
        if isinstance(src, Path):
            plan = [S_NAMED, S_CARVE]
        elif self._is_disc():
            if src.disc == devmod.DISC_AUDIO:
                plan = [S_AUDIO]
            elif src.disc == devmod.DISC_VCD:
                plan = [S_VCD]
            elif src.disc in (devmod.DISC_DVD_VIDEO, devmod.DISC_DVD_VR, devmod.DISC_BDAV):
                plan = [S_IMAGE, S_NAMED, S_PULL]
            else:
                plan = [S_IMAGE, S_NAMED, S_CARVE]
        else:
            plan = [S_HEALTH, S_IMAGE, S_NAMED, S_CARVE]
        if self.carve_mode.currentData() == "off" and S_CARVE in plan:
            plan.remove(S_CARVE)
        self.plan = plan
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.rows = {name: StageRow(self.grid, i, name) for i, name in enumerate(plan)}

    def _update_controls(self) -> None:
        dev = isinstance(self.source, devmod.Device) and not self._is_disc()
        self.form.setRowVisible(self.modes, dev)
        self.r_image.setEnabled(dev and not self.running)
        allowed, why = safety.direct_scan_allowed(self.health)
        self.r_direct.setEnabled(dev and allowed and not self.running)
        if not allowed:
            self.r_image.setChecked(True)
            self.r_direct.setToolTip(why)
        self.carve_mode.setEnabled(not self.running)
        self.out_edit.setEnabled(not self.running)
        self.start_btn.setEnabled(self.source is not None and not self.running)
        self.cancel_btn.setEnabled(self.running)

    # --- preflight -------------------------------------------------------------

    def _source_path(self) -> str:
        return str(self.source) if isinstance(self.source, Path) else self.source.path

    def _job_dir(self) -> Path:
        out = Path(self.out_edit.text()).expanduser()
        if isinstance(self.source, Path):
            label = self.source.stem
        else:
            label = "-".join(x for x in (self.source.model, self.source.serial) if x) or self.source.name
        return out / undelete.job_dir_name(label)

    def _needed_tools(self) -> list[str]:
        need = []
        for s in self.plan:
            need += {S_HEALTH: ["smartctl"], S_IMAGE: ["ddrescue"], S_NAMED: ["fls", "icat", "mmls", "fsstat"],
                     S_CARVE: ["photorec"], S_AUDIO: ["cdparanoia"], S_VCD: ["vcdxrip"], S_PULL: ["icat"]}[s]
        return need

    def _preflight(self) -> bool:
        reason = self.state.missing_reason(*self._needed_tools())
        if reason:
            self.banner.show_message(reason, "error")
            return False
        out = Path(self.out_edit.text()).expanduser()
        try:
            safety.check_output_location(self._source_path(), str(out))
        except safety.SafetyError as exc:
            self.banner.show_message(str(exc), "error")  # rule 1: no override
            return False
        if isinstance(self.source, devmod.Device) and not self._is_disc():
            mounts = safety.rw_mounts(self.source)
            if mounts and not self._handle_mounts(mounts):
                return False
        return True

    def _handle_mounts(self, mounts) -> bool:
        where = "\n".join(f"{node} on {mp}" for node, mp in mounts)
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Mounted read-write")
        box.setText("This drive is mounted read-write:\n" + where +
                    "\n\nEvery write the system makes can land on a deleted file. Unmount it first.")
        box.setDetailedText("To keep it mounted but read-only instead, run:\n" +
                            "\n".join(safety.remount_ro_command(mp) for _n, mp in mounts))
        unmount = box.addButton("Unmount", QMessageBox.ButtonRole.AcceptRole)
        anyway = box.addButton("Continue Anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        clicked = box.clickedButton()
        if clicked is anyway:
            return True
        if clicked is not unmount:
            return False
        for node, mp in mounts:
            res = run_command(safety.unmount_command(node, mp), log=self.log.append)
            if not res.ok:
                self.banner.show_message(f"Could not unmount {mp}: {res.stderr.strip()}. Unmount it in your file "
                                         "manager and press Start again.", "error")
                return False
        return True

    # --- running ---------------------------------------------------------------

    def start(self) -> None:
        if not self.source or self.running:
            return
        self._plan()
        if not self._preflight():
            return
        self.job_dir = self._job_dir()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        if self._is_disc() or not isinstance(self.source, devmod.Device):
            pass
        elif self.r_direct.isChecked():
            if not self._confirm_direct():
                return
            self.rows[S_IMAGE].finish("skipped", "skipped, scanning directly")
        self.result = ScanResult(source=self._source_path(), root=False, job_dir=self.job_dir)
        self.running = True
        self.job = self.state.new_job(f"Recover from {self._source_path()}")
        self.log.clear()
        self.banner.show_message(f"Working in {self.job_dir}", "info")
        self._update_controls()
        self.queue = [s for s in self.plan if self.rows[s].state == "waiting"]
        self._next()

    def _confirm_direct(self) -> bool:
        return QMessageBox.warning(
            self, "Scan directly?",
            "Scanning the drive directly reads it several times. On a healthy drive that is fine. On a drive "
            "that is starting to fail, every read can be its last. Imaging first reads it once.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Ok

    def _log(self, line: str) -> None:
        self.log.append(line)
        if self.job:
            self.state.job_log(self.job, line)

    def _tick(self) -> None:
        for r in self.rows.values():
            r.tick()
        if self.running and self.queue and self.queue[0] == S_CARVE:
            n = len(photorec.carved_files(self.job_dir / "carved" / "recup_dir"))
            self.rows[S_CARVE].note(f"{n} files so far")

    def _next(self) -> None:
        if not self.running:
            return
        if not self.queue:
            self._finish(DONE)
            return
        stage = self.queue[0]
        handler = {S_HEALTH: self._do_health, S_IMAGE: self._do_image, S_NAMED: self._do_named,
                   S_CARVE: self._do_carve, S_PULL: self._do_pull, S_AUDIO: self._do_audio,
                   S_VCD: self._do_vcd}[stage]
        handler()

    def _stage_done(self, stage: str, state: str = DONE, text: str = "") -> None:
        self.rows[stage].finish(state, text)
        if self.queue and self.queue[0] == stage:
            self.queue.pop(0)
        if state in (FAILED, CANCELLED):
            self._finish(state, text)
        else:
            self._next()

    def cancel(self) -> None:
        if not self.running:
            return
        if self.job and self.job.cancel:
            self.job.cancel.cancel()
        if self.proc:
            self.proc.cancel()

    def _finish(self, state: str, text: str = "") -> None:
        self.running = False
        self.proc = None
        for r in self.rows.values():
            if r.state == "running":
                r.finish(state)
        resume = ""
        if state == CANCELLED:
            resume = " Press Start again with the same output folder to pick up where it stopped."
        self.state.finish_job(self.job, state, text)
        if self.result and (self.result.named or self.result.carved):
            self.results_btn.setEnabled(True)
            self.state.results_ready.emit(self.result)
        level = {DONE: "ok", CANCELLED: "warn"}.get(state, "error")
        msg = {DONE: "Finished.", CANCELLED: "Stopped." + resume}.get(state, f"Failed: {text}")
        if self.result and self.result.note:
            msg += " " + self.result.note
        self.banner.show_message(msg, level)
        self._update_controls()

    def _thread(self, stage: str, fn, done, indeterminate: bool = True) -> None:
        self.rows[stage].start(indeterminate)
        bridge = run_in_thread(fn)
        bridge.line.connect(self._log)
        bridge.finished.connect(done)
        bridge.failed.connect(lambda e: self._stage_done(stage, FAILED, e))
        bridge.start()
        self._bridge = bridge  # keep a reference while it runs

    def _process(self, stage: str, argv: list[str], parser, done, cwd: str = "") -> None:
        self.rows[stage].start(parser is None)
        proc = ProcessJob(argv, parser=parser, cwd=cwd, parent=self)
        proc.line.connect(self._log)
        proc.progress.connect(lambda f: self.rows[stage].set(f))
        proc.finished.connect(done)
        self.proc = proc
        proc.start()

    # --- stages ------------------------------------------------------------------

    def _do_health(self) -> None:
        dev = self.source

        def run(bridge):
            return smart.check(dev.path, log=bridge.line.emit)

        def done(h):
            self.health = h
            dev.smart = h
            ok, why = safety.direct_scan_allowed(h)
            if not ok and S_IMAGE in self.plan and self.rows[S_IMAGE].state == "skipped":
                self._log(why)
                self.rows[S_IMAGE].state = "waiting"
                self.rows[S_IMAGE].bar.setFormat("waiting")
                self.queue.insert(1, S_IMAGE)
                self.banner.show_message(why + " Switched to imaging first.", "warn")
            self._stage_done(S_HEALTH, DONE, h.text())

        self._thread(S_HEALTH, run, done)

    def _do_image(self) -> None:
        disc = self._is_disc()
        image = self.job_dir / ("disc.iso" if disc else "drive.img")
        mapfile = image.with_suffix(".map")
        size = self.source.size or 0
        resuming = mapfile.exists() and mapfile.stat().st_size > 0
        if not resuming:
            ok, free = safety.free_space_ok(str(self.job_dir), size)
            if not ok:
                msg = (f"Not enough room to image: the source is {human_size(size)} and {self.job_dir} has "
                       f"{human_size(free)} free.")
                allowed, why = safety.direct_scan_allowed(self.health)
                if disc or not allowed:
                    self._stage_done(S_IMAGE, FAILED, msg + (" " + why if why else ""))
                    return
                if QMessageBox.question(self, "Not enough room", msg + "\n\nScan the drive directly instead?") \
                        != QMessageBox.StandardButton.Yes or not self._confirm_direct():
                    self._stage_done(S_IMAGE, FAILED, msg)
                    return
                self._stage_done(S_IMAGE, "skipped", "skipped, scanning directly")
                return
        else:
            summary = ddrescue.read_mapfile(mapfile)
            self._log(f"Resuming from {mapfile}: {human_size(summary.rescued)} already rescued.")
        ddrescue.prepare_files(image, mapfile)
        try:
            passes = ddrescue.passes(self.source.path, image, mapfile, disc=disc,
                                     root=not os.access(self.source.path, os.R_OK))
        except privilege.PrivilegeError as exc:
            self._stage_done(S_IMAGE, FAILED, str(exc))
            return
        self._image_passes = passes
        self._image_target = image
        self._run_image_pass(0)

    def _run_image_pass(self, i: int) -> None:
        argv = self._image_passes[i]
        label = ["first pass, skipping bad areas", "retrying bad areas"][i]
        self._log(f"ddrescue {label}")

        def done(code: int):
            if code == -1:
                self._stage_done(S_IMAGE, CANCELLED)
                return
            if code != 0:
                self._stage_done(S_IMAGE, FAILED, f"ddrescue exited with {code}")
                return
            if i + 1 < len(self._image_passes):
                self._run_image_pass(i + 1)
                return
            image = self._image_target
            mapfile = image.with_suffix(".map")
            self._chown([image, mapfile])
            summary = ddrescue.read_mapfile(mapfile)
            note = f"{human_size(summary.rescued)} rescued"
            if summary.bad:
                note += f", {human_size(summary.bad)} unreadable"
                self.result.note = ("Some areas could not be read. Clean the disc or try another drive and "
                                    "press Start again: only the bad areas are retried.") if self._is_disc() else ""
            self.result.source = str(image)
            self.result.root = False
            self._stage_done(S_IMAGE, DONE, note)

        self.rows[S_IMAGE].start(False)
        proc = ProcessJob(argv, parser=ddrescue.parse_progress, parent=self)
        proc.line.connect(self._log)
        proc.progress.connect(lambda f: self.rows[S_IMAGE].set(f))
        proc.finished.connect(done)
        self.proc = proc
        proc.start()

    def _chown(self, paths: list[Path]) -> None:
        """Hand root-written files back to the user, inside the job folder only."""
        mine = all(p.exists() and p.stat().st_uid == os.getuid() for p in paths)
        if mine or os.getuid() == 0:
            return
        try:
            argv = privilege.chown_back([str(p) for p in paths], str(self.job_dir))
        except privilege.PrivilegeError as exc:
            self._log(f"could not hand files back: {exc}")
            return
        run_command(argv, log=self._log)

    def _direct(self) -> bool:
        """True when later stages read the device itself as root."""
        return self.result.source.startswith("/dev/") and not os.access(self.result.source, os.R_OK)

    def _do_named(self) -> None:
        src = self.result.source
        root = self._direct()
        cancel = self.job.cancel
        disc_video = S_PULL in self.plan

        def run(bridge):
            scan = undelete.named_scan(src, root=root, log=bridge.line.emit, cancel=cancel)
            if disc_video:
                # An intact disc's files are not deleted: list everything.
                from filewinch.backends import tsk
                scan.entries = []
                for vol in scan.volumes:
                    if scan.fstypes.get(vol.offset):
                        scan.entries += tsk.list_deleted(src, vol.offset, root=root, log=bridge.line.emit,
                                                         cancel=cancel, deleted_only=False)
            return scan

        def done(scan):
            self.result.root = root
            self.result.named = [e for e in scan.entries if not e.is_dir]
            self.result.fstypes = scan.fstypes
            self._named_scan = scan
            if not scan.readable:
                text = "no readable filesystem, carving will do the work"
            else:
                fs = ", ".join(sorted({v for v in scan.fstypes.values() if v}))
                text = f"{len(self.result.named)} files listed ({fs})"
                if any("Ext" in v or "EXT" in v for v in scan.fstypes.values()):
                    text += "; ext3/4 keep names only, contents come from carving"
            self._stage_done(S_NAMED, DONE, text)

        self._thread(S_NAMED, run, done)

    def _do_carve(self) -> None:
        src = self.result.source
        root = self._direct()
        mode_pick = self.carve_mode.currentData()
        scan = getattr(self, "_named_scan", None)
        part = undelete.freespace_partition(scan) if (mode_pick == "auto" and scan) else None
        mode = photorec.FREE if part else photorec.WHOLE
        out = self.job_dir / "carved"
        cancel = self.job.cancel
        self._log(f"Carving {'free space only' if part else 'the whole source'}.")
        settings = self.state.settings

        def run(bridge):
            files = undelete.carve(src, out, mode=mode, partition=part, root=root, log=bridge.line.emit,
                                   cancel=cancel)
            if cancel.cancelled:
                return None
            return files

        def done(files):
            if files is None:
                self._stage_done(S_CARVE, CANCELLED)
                return
            if root:
                self._chown([out])
            sorted_files = undelete.sort_by_type(files, out)
            for d in out.glob("recup_dir.*"):
                if d.is_dir() and not any(p for p in d.iterdir() if p.name != "report.xml"):
                    shutil.rmtree(d, ignore_errors=True)
            if settings.rename_by_date and sorted_files and self.state.report.available("exiftool"):
                media = [p for p in sorted_files if p.suffix.lower().lstrip(".") in
                         {"jpg", "jpeg", "heic", "png", "tif", "cr2", "nef", "arw", "dng", "mp4", "mov", "m4v", "3gp"}]
                if media:
                    run_command(undelete.rename_by_date_argv(media), log=self._log)
                    sorted_files = [p for p in (out / "by-type").rglob("*") if p.is_file()]
            self.result.carved = sorted_files
            if settings.auto_repair_videos:
                vids = [p for p in sorted_files if undelete.is_video(p)]
                if vids:
                    self.state.repair_queue.emit(vids)
            self._stage_done(S_CARVE, DONE, f"{len(sorted_files)} files")

        self._thread(S_CARVE, run, done)

    def _do_pull(self) -> None:
        src = self.result.source
        root = self.result.root
        entries = undelete.disc_video_entries(self.result.named)
        dest = self.job_dir / "disc-video"
        cancel = self.job.cancel
        if not entries:
            self._stage_done(S_PULL, DONE, "no VOB, VRO or M2TS files found")
            return

        def run(bridge):
            return undelete.save_named(src, entries, dest, root=root, log=bridge.line.emit, cancel=cancel)

        def done(saved):
            self.result.carved = saved
            if saved:
                self.state.repair_queue.emit(saved)
            self._stage_done(S_PULL, DONE, f"{len(saved)} files sent to Repair")

        self._thread(S_PULL, run, done)

    def _rip(self, stage: str, argv: list[str], collect, to_repair: bool) -> None:
        dest = self.job_dir / ("audio-cd" if stage == S_AUDIO else "video-cd")
        dest.mkdir(parents=True, exist_ok=True)

        def done(code: int):
            if code == -1:
                self._stage_done(stage, CANCELLED)
                return
            self._chown([dest])
            files = collect(dest)
            self.result.carved = files
            if stage == S_AUDIO:
                report = cdparanoia.report(dest)
                bad = [l for l in report.splitlines() if "skip" in l.lower() or "error" in l.lower()]
                self.result.note = (f"cdparanoia logged {len(bad)} problem lines, see cdparanoia.log in "
                                    f"{dest}." if bad else "Every track read clean.")
            if to_repair and files:
                self.state.repair_queue.emit(files)
            state = DONE if files else FAILED
            self._stage_done(stage, state, f"{len(files)} tracks" if files else f"no tracks (exit {code})")

        self._process(stage, argv, None, done, cwd=str(dest))

    def _do_audio(self) -> None:
        try:
            argv = cdparanoia.argv(self.source.path, root=not os.access(self.source.path, os.R_OK))
        except privilege.PrivilegeError as exc:
            self._stage_done(S_AUDIO, FAILED, str(exc))
            return
        self._rip(S_AUDIO, argv, cdparanoia.tracks, to_repair=False)

    def _do_vcd(self) -> None:
        try:
            argv = vcd.argv(self.source.path, root=not os.access(self.source.path, os.R_OK))
        except privilege.PrivilegeError as exc:
            self._stage_done(S_VCD, FAILED, str(exc))
            return
        self._rip(S_VCD, argv, vcd.tracks, to_repair=True)
