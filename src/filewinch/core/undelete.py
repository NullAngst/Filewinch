"""The undelete steps, shared by the GUI and the tests.

Named recovery first (Sleuth Kit), then carving (PhotoRec) for whatever the
filesystem lost. Safety checks live in core/safety.py and are applied by the
caller before any of this runs.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from filewinch.backends import photorec, tsk
from filewinch.core.jobs import CancelToken, run_command

# Filesystem families, by what fsstat reports
NAMED_OK = ("FAT", "exFAT", "NTFS", "Ext2", "EXT2", "HFS", "APFS", "ISO9660", "UDF")


@dataclass
class NamedScan:
    volumes: list = field(default_factory=list)
    entries: list = field(default_factory=list)
    fstypes: dict = field(default_factory=dict)   # offset -> fstype

    @property
    def readable(self) -> bool:
        return any(self.fstypes.values())


def named_scan(source: str, root: bool = False, log=None, cancel: CancelToken | None = None) -> NamedScan:
    scan = NamedScan(volumes=tsk.volumes(source, root=root))
    for vol in scan.volumes:
        fs = tsk.fstype(source, vol.offset, root=root)
        scan.fstypes[vol.offset] = fs
        if not fs:
            continue
        for e in tsk.list_deleted(source, vol.offset, root=root, log=log, cancel=cancel):
            e.volume = f"{vol.description} ({fs})"
            scan.entries.append(e)
    return scan


def save_named(source: str, entries: list, dest: Path, root: bool = False, log=None,
               cancel: CancelToken | None = None) -> list[Path]:
    saved = []
    multi = len({e.offset for e in entries}) > 1
    for e in entries:
        if cancel and cancel.cancelled:
            break
        if e.is_dir:
            continue
        base = dest / f"volume-{e.offset}" if multi else dest
        p = tsk.save(source, e, base, root=root, log=log, cancel=cancel)
        if p:
            saved.append(p)
    return saved


def _strip_inter(session_dir: Path) -> None:
    """PhotoRec appends 'inter' to an interrupted session, which would drop it
    into its keyboard menu. Remove it so the resume can run unattended."""
    ses = session_dir / "photorec.ses"
    try:
        lines = ses.read_text().split("\n")
    except OSError:
        return
    if len(lines) > 1 and lines[1].endswith(",inter"):
        lines[1] = lines[1][: -len(",inter")]
        ses.write_text("\n".join(lines))


def _replace(new_prefix: Path, old_prefix: Path) -> None:
    """A finished rerun is a complete carve, so it replaces the interrupted output.

    Keeping the old files would also keep whatever PhotoRec was halfway
    through writing when it was stopped.
    """
    for d in old_prefix.parent.glob(old_prefix.name + ".*"):
        shutil.rmtree(d, ignore_errors=True)
    for d in sorted(new_prefix.parent.glob(new_prefix.name + ".*")):
        d.rename(old_prefix.parent / (old_prefix.name + d.name[len(new_prefix.name):]))


def carve(source: str, out_dir: Path, mode: str = photorec.WHOLE, partition: int | None = None,
          root: bool = False, log=None, cancel: CancelToken | None = None) -> list[Path]:
    """Run PhotoRec into out_dir/recup_dir.N. Resumes if a session is waiting."""
    out_dir.mkdir(parents=True, exist_ok=True)
    session = out_dir / ".photorec-session"
    session.mkdir(exist_ok=True)
    prefix = out_dir / "recup_dir"
    if photorec.has_session(session):
        if log:
            log("Found an interrupted carve. Resuming from PhotoRec's session file.")
        _strip_inter(session)
        run_command(photorec.resume_argv(prefix, root=root), log=log, cancel=cancel, cwd=str(session),
                    keep_stdout=False)
        if cancel and cancel.cancelled:
            return photorec.carved_files(prefix)
        if photorec.has_session(session):
            # PhotoRec could not pick up its own session (7.1 stops in its
            # keyboard menu). Carve again from the start, which gives the same
            # file set as one uninterrupted run.
            if log:
                log("PhotoRec did not finish the resume. Carving again from the start.")
            (session / "photorec.ses").unlink(missing_ok=True)
            rerun = out_dir / "rerun_dir"
            for d in out_dir.glob("rerun_dir.*"):
                shutil.rmtree(d, ignore_errors=True)
            res = run_command(photorec.argv(source, rerun, mode, partition, root), log=log, cancel=cancel,
                              cwd=str(session), keep_stdout=False)
            if res.ok and not photorec.has_session(session):
                _replace(rerun, prefix)
        return photorec.carved_files(prefix)
    run_command(photorec.argv(source, prefix, mode, partition, root), log=log, cancel=cancel,
                cwd=str(session), keep_stdout=False)
    return photorec.carved_files(prefix)


def sort_by_type(files: list[Path], out_dir: Path) -> list[Path]:
    """Move carved files into one folder per extension."""
    moved = []
    for f in files:
        ext = f.suffix.lstrip(".").lower() or "unknown"
        dest_dir = out_dir / "by-type" / ext
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f.name
        n = 1
        while dest.exists():
            dest = dest_dir / f"{f.stem}.{n}{f.suffix}"
            n += 1
        shutil.move(str(f), str(dest))
        moved.append(dest)
    return moved


def rename_by_date_argv(paths: list[Path]) -> list[str]:
    """exiftool renames by capture date. %-c adds a counter on clashes.

    Files with no date tag are left alone, which exiftool reports and skips.
    """
    tool = shutil.which("exiftool") or "exiftool"
    return [tool, "-P", "-q", "-q", "-d", "%Y-%m-%d_%H%M%S%%-c.%%le",
            "-FileName<CreateDate", "-FileName<DateTimeOriginal", "--", *[str(p) for p in paths]]


VIDEO_EXTS = {"mp4", "mov", "m4v", "3gp", "mkv", "webm", "avi", "mpg", "mpeg", "vob", "ts", "m2ts", "mts", "wmv", "flv"}


def is_video(path: Path) -> bool:
    return path.suffix.lstrip(".").lower() in VIDEO_EXTS


# --- job folders and discs -------------------------------------------------------

def job_dir_name(source_label: str) -> str:
    """Stable per source, so restarting the same recovery finds its mapfile and
    PhotoRec session and resumes instead of starting over."""
    import re
    return re.sub(r"[^A-Za-z0-9._-]+", "_", source_label).strip("_") or "source"


# Files on a home-recorded video disc that go straight to Repair.
DISC_VIDEO_PATTERNS = (
    ("VIDEO_TS/", (".vob",)),
    ("DVD_RTAV/", (".vro",)),
    ("BDMV/STREAM/", (".m2ts",)),
    ("BDAV/STREAM/", (".m2ts",)),
)


def disc_video_entries(entries: list) -> list:
    out = []
    for e in entries:
        if e.is_dir:
            continue
        up = e.path.upper()
        for prefix, exts in DISC_VIDEO_PATTERNS:
            if up.startswith(prefix) and up.lower().endswith(exts):
                out.append(e)
    return out


def freespace_partition(scan: "NamedScan") -> int | None:
    """PhotoRec partition number for a free-space-only carve, or None for whole.

    Only a bare filesystem image (one volume at offset 0) maps cleanly onto
    PhotoRec's numbering, and only for filesystems PhotoRec can read the
    allocation map of. Everything else carves the whole source.
    """
    if len(scan.volumes) != 1 or scan.volumes[0].offset != 0:
        return None
    fs = (scan.fstypes.get(0) or "").upper()
    if any(k in fs for k in ("FAT", "NTFS", "EXT")):
        return 1
    return None
