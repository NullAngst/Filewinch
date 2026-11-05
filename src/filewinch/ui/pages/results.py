"""Results: browse what was found, preview it, save it."""

from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
from pathlib import Path

from PyQt6.QtCore import QDate, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                             QPlainTextEdit, QSpinBox, QSplitter, QStackedWidget, QTreeWidget, QTreeWidgetItem,
                             QVBoxLayout, QWidget)

from filewinch.backends import tsk
from filewinch.core import safety, undelete
from filewinch.core.jobs import run_command
from filewinch.ui.state import run_in_thread
from filewinch.ui.widgets.common import Banner, button, hint, human_size, title

KINDS = {
    "Images": {"jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "heic", "cr2", "cr3", "nef", "arw", "dng",
               "orf", "rw2", "raf", "pef", "srw"},
    "Video": undelete.VIDEO_EXTS,
    "Audio": {"mp3", "flac", "wav", "aif", "aiff", "ogg", "opus", "m4a", "wma", "aac"},
    "Documents": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "txt", "rtf", "csv",
                  "md", "html", "htm", "xml", "json", "epub"},
    "Archives": {"zip", "7z", "rar", "gz", "bz2", "xz", "tar", "iso"},
}
TEXT_EXTS = {"txt", "csv", "md", "log", "xml", "html", "htm", "json", "ini", "cfg", "conf", "py", "sh", "srt"}
IMAGE_PREVIEW = {"jpg", "jpeg", "png", "gif", "bmp", "webp", "tif", "tiff"}

ROLE = Qt.ItemDataRole.UserRole


def kind_of(name: str) -> str:
    ext = Path(name).suffix.lower().lstrip(".")
    for k, exts in KINDS.items():
        if ext in exts:
            return k
    return "Other"


class ResultsPage(QWidget):
    name = "results"
    label = "Results"

    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        self.result = None
        self.preview_dir = Path(tempfile.mkdtemp(prefix="filewinch-preview-"))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.addWidget(title("Results"))
        self.summary = hint("Nothing scanned yet.")
        lay.addWidget(self.summary)
        self.banner = Banner()
        lay.addWidget(self.banner)

        filt = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItems(["All types", *KINDS.keys(), "Other"])
        self.min_kb = QSpinBox()
        self.min_kb.setRange(0, 10_000_000)
        self.min_kb.setSuffix(" KB min")
        self.use_date = QCheckBox("Modified between")
        self.date_from = QDateEdit(QDate.currentDate().addYears(-1))
        self.date_to = QDateEdit(QDate.currentDate())
        for d in (self.date_from, self.date_to):
            d.setCalendarPopup(True)
            d.setDisplayFormat("yyyy-MM-dd")
        self.use_date.setToolTip("Dates come from the filesystem, so this filters named files only. "
                                 "Carved files have no original date until renamed by capture date.")
        for w in (QLabel("Show"), self.kind, self.min_kb, self.use_date, self.date_from, QLabel("and"), self.date_to):
            filt.addWidget(w)
        filt.addStretch(1)
        lay.addLayout(filt)
        for sig in (self.kind.currentIndexChanged, self.min_kb.valueChanged, self.use_date.toggled,
                    self.date_from.dateChanged, self.date_to.dateChanged):
            sig.connect(self._fill)

        split = QSplitter(Qt.Orientation.Horizontal)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "Where"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.currentItemChanged.connect(self._preview)
        self.tree.itemChanged.connect(self._check_children)
        split.addWidget(self.tree)
        self.preview = QStackedWidget()
        self.img = QLabel("Pick a file to preview it.")
        self.img.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.img.setMinimumWidth(280)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setObjectName("Log")
        self.preview.addWidget(self.img)
        self.preview.addWidget(self.text)
        split.addWidget(self.preview)
        split.setSizes([640, 340])
        lay.addWidget(split, 1)

        bar = QHBoxLayout()
        self.save_btn = button("Save Selected", primary=True)
        self.save_btn.clicked.connect(self._save)
        self.rename_btn = button("Rename by Date")
        self.rename_btn.clicked.connect(self._rename)
        self.repair_btn = button("Send Videos to Repair")
        self.repair_btn.clicked.connect(self._send_videos)
        self.all_btn = button("Select All Shown")
        self.all_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        self.none_btn = button("Select None")
        self.none_btn.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        for b in (self.all_btn, self.none_btn):
            bar.addWidget(b)
        bar.addStretch(1)
        for b in (self.rename_btn, self.repair_btn, self.save_btn):
            bar.addWidget(b)
        lay.addLayout(bar)
        reason = state.missing_reason("exiftool")
        self.rename_btn.setEnabled(not reason)
        self.rename_btn.setToolTip(reason or "Rename checked carved photos and videos by capture date")
        state.results_ready.connect(self.show_result)
        self._update_buttons()

    # --- filling ---------------------------------------------------------------

    def show_result(self, result) -> None:
        self.result = result
        n, c = len(result.named), len(result.carved)
        self.summary.setText(f"From {result.source}: {n} deleted files with names, {c} carved or ripped files. "
                             f"Working folder {result.job_dir}.")
        self._fill()

    def _passes(self, name: str, size: int, mtime: int | None) -> bool:
        k = self.kind.currentText()
        if k != "All types" and kind_of(name) != k:
            return False
        if size < self.min_kb.value() * 1000:
            return False
        if self.use_date.isChecked() and mtime is not None:
            d = _dt.date.fromtimestamp(mtime) if mtime else None
            lo = self.date_from.date().toPyDate()
            hi = self.date_to.date().toPyDate()
            if d is None or not (lo <= d <= hi):
                return False
        return True

    def _folder(self, root: QTreeWidgetItem, parts: tuple, cache: dict) -> QTreeWidgetItem:
        node = root
        for i in range(len(parts)):
            key = parts[: i + 1]
            if key not in cache:
                child = QTreeWidgetItem([parts[i]])
                child.setFlags(child.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
                child.setCheckState(0, Qt.CheckState.Unchecked)
                node.addChild(child)
                cache[key] = child
            node = cache[key]
        return node

    def _fill(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        r = self.result
        if not r:
            self.tree.blockSignals(False)
            return
        named_root = QTreeWidgetItem([f"By original folder ({len(r.named)})"])
        carved_root = QTreeWidgetItem([f"Carved and ripped, by type ({len(r.carved)})"])
        for top in (named_root, carved_root):
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            top.setCheckState(0, Qt.CheckState.Unchecked)
        cache: dict = {}
        shown = 0
        for e in r.named:
            if not self._passes(e.path, e.size, e.mtime):
                continue
            parts = Path(e.path).parts
            parent = self._folder(named_root, (e.volume,) + parts[:-1] if len({x.offset for x in r.named}) > 1
                                  else parts[:-1], cache)
            when = _dt.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d %H:%M") if e.mtime else ""
            item = QTreeWidgetItem([parts[-1], human_size(e.size), when, "on the source"])
            item.setData(0, ROLE, ("named", e))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            parent.addChild(item)
            shown += 1
        ccache: dict = {}
        for p in r.carved:
            if not p.exists():
                continue
            size = p.stat().st_size
            if not self._passes(p.name, size, None):
                continue
            folder = p.parent.name if p.parent.parent.name == "by-type" else p.parent.name
            parent = self._folder(carved_root, (folder,), ccache)
            item = QTreeWidgetItem([p.name, human_size(size), "", str(p.parent)])
            item.setData(0, ROLE, ("file", p))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(0, Qt.CheckState.Unchecked)
            parent.addChild(item)
            shown += 1
        for top in (named_root, carved_root):
            if top.childCount():
                self.tree.addTopLevelItem(top)
                top.setExpanded(True)
        self.tree.blockSignals(False)
        self.banner.show_message("" if shown else "Nothing matches these filters.", "info")
        self._update_buttons()

    def _check_children(self, item, col) -> None:
        self._update_buttons()

    def _leaves(self, checked_only: bool = True) -> list[tuple[str, object]]:
        out = []

        def walk(node):
            for i in range(node.childCount()):
                ch = node.child(i)
                data = ch.data(0, ROLE)
                if data and (not checked_only or ch.checkState(0) == Qt.CheckState.Checked):
                    out.append(data)
                walk(ch)

        walk(self.tree.invisibleRootItem())
        return out

    def _set_all(self, state) -> None:
        for i in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(i).setCheckState(0, state)

    def _update_buttons(self) -> None:
        picked = self._leaves()
        self.save_btn.setEnabled(bool(picked))
        files = [d for k, d in self._leaves(False) if k == "file"]
        self.repair_btn.setEnabled(any(undelete.is_video(p) for p in files))

    # --- preview ---------------------------------------------------------------

    def _preview(self, item, _prev=None) -> None:
        data = item.data(0, ROLE) if item else None
        if not data:
            self.preview.setCurrentWidget(self.img)
            self.img.setPixmap(QPixmap())
            self.img.setText("Pick a file to preview it.")
            return
        kind, obj = data
        self.img.setText("Loading preview...")
        self.preview.setCurrentWidget(self.img)
        r = self.result
        tmp = self.preview_dir

        def run(bridge):
            if kind == "named":
                dest = tmp / f"{obj.offset}-{obj.inode.replace('/', '_')}{Path(obj.path).suffix}"
                if not dest.exists():
                    run_command(tsk.icat_argv(r.source, obj, r.root), stdout_file=str(dest), timeout=120)
                path = dest
            else:
                path = obj
            return make_preview(path, tmp)

        bridge = run_in_thread(run)
        bridge.finished.connect(self._show_preview)
        bridge.failed.connect(lambda e: self.img.setText(f"No preview: {e}"))
        bridge.start()
        self._pbridge = bridge

    def _show_preview(self, res) -> None:
        kind, payload = res
        if kind == "image":
            pm = QPixmap(str(payload))
            if pm.isNull():
                self.img.setText("No preview: the image does not decode.")
                return
            self.img.setPixmap(pm.scaled(self.img.width() - 10, self.img.height() - 10,
                                         Qt.AspectRatioMode.KeepAspectRatio,
                                         Qt.TransformationMode.SmoothTransformation))
            self.preview.setCurrentWidget(self.img)
        elif kind == "text":
            self.text.setPlainText(payload)
            self.preview.setCurrentWidget(self.text)
        else:
            self.img.setPixmap(QPixmap())
            self.img.setText(payload)

    # --- actions ---------------------------------------------------------------

    def _save(self) -> None:
        r = self.result
        picked = self._leaves()
        if not r or not picked:
            return
        default = r.job_dir / "saved"
        dest = QFileDialog.getExistingDirectory(self, "Save the selected files to", str(r.job_dir))
        dest = Path(dest) / "saved" if dest else default
        try:
            safety.check_output_location(r.source, str(dest))
        except safety.SafetyError as exc:
            self.banner.show_message(str(exc), "error")
            return
        dest.mkdir(parents=True, exist_ok=True)
        named = [obj for k, obj in picked if k == "named"]
        files = [obj for k, obj in picked if k == "file"]
        job = self.state.new_job(f"Save {len(picked)} files")

        def run(bridge):
            saved = undelete.save_named(r.source, named, dest / "named", root=r.root, log=bridge.line.emit,
                                        cancel=job.cancel) if named else []
            for p in files:
                target = dest / p.parent.name / p.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if p.resolve() != target.resolve():
                    shutil.copy2(p, target)
                saved.append(target)
            return saved

        bridge = run_in_thread(run)
        bridge.line.connect(lambda l: self.state.job_log(job, l))

        def done(saved):
            self.state.finish_job(job, "done", f"{len(saved)} saved to {dest}")
            self.banner.show_message(f"Saved {len(saved)} of {len(picked)} files to {dest}.",
                                     "ok" if len(saved) == len(picked) else "warn")

        bridge.finished.connect(done)
        bridge.failed.connect(lambda e: (self.state.finish_job(job, "failed", e),
                                         self.banner.show_message(e, "error")))
        bridge.start()
        self._sbridge = bridge

    def _carved_targets(self) -> list[Path]:
        picked = [obj for k, obj in self._leaves() if k == "file"]
        return picked or [obj for k, obj in self._leaves(False) if k == "file"]

    def _rename(self) -> None:
        files = [p for p in self._carved_targets() if kind_of(p.name) in ("Images", "Video")]
        if not files:
            self.banner.show_message("No carved photos or videos to rename.", "info")
            return
        job = self.state.new_job(f"Rename {len(files)} files by capture date")
        res = run_command(undelete.rename_by_date_argv(files), log=lambda l: self.state.job_log(job, l))
        self.state.finish_job(job, "done" if res.code in (0, 1) else "failed")
        r = self.result
        r.carved = [p for p in (r.job_dir / "carved" / "by-type").rglob("*") if p.is_file()] or r.carved
        self._fill()
        self.banner.show_message("Renamed what had a capture date. Files without one keep their carved name.", "ok")

    def _send_videos(self) -> None:
        vids = [p for p in self._carved_targets() if undelete.is_video(p)]
        if vids:
            self.state.repair_queue.emit(vids)
            self.state.navigate.emit("repair")


def make_preview(path: Path, tmp: Path) -> tuple[str, object]:
    """('image', path) or ('text', str) or ('none', reason)."""
    if not path.exists() or path.stat().st_size == 0:
        return "none", "No preview: the file is empty."
    ext = path.suffix.lower().lstrip(".")
    if ext in IMAGE_PREVIEW:
        return "image", path
    if ext in undelete.VIDEO_EXTS and shutil.which("ffmpeg"):
        out = tmp / (path.name + ".thumb.png")
        # First good frame: skip frames that fail to decode, then let the thumbnail
        # filter pick a representative one from the first few.
        run_command(["ffmpeg", "-nostdin", "-v", "error", "-y", "-err_detect", "ignore_err", "-i", str(path),
                     "-vf", "thumbnail=25,scale=480:-2", "-frames:v", "1", str(out)], timeout=60)
        if out.exists() and out.stat().st_size:
            return "image", out
        return "none", "No preview: no frame decodes."
    if ext == "pdf" and shutil.which("mutool"):
        out = tmp / (path.name + ".page1.png")
        run_command(["mutool", "draw", "-q", "-r", "60", "-o", str(out), str(path), "1"], timeout=60)
        if out.exists() and out.stat().st_size:
            return "image", out
    if ext in TEXT_EXTS or ext in ("pdf",):
        with open(path, "rb") as fh:
            raw = fh.read(16384)
        if ext == "pdf":
            return "text", "PDF, first bytes:\n" + raw[:2000].decode("latin-1", "replace")
        return "text", raw.decode("utf-8", "replace")
    if shutil.which("file"):
        res = run_command(["file", "-b", str(path)], timeout=30)
        return "none", f"No preview for this type.\n{res.stdout.strip()}"
    return "none", "No preview for this type."
