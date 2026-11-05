"""PhotoRec: signature carving, run with /cmd so it needs no keyboard.

The session file (photorec.ses) lives in the job's session folder, which is
PhotoRec's working directory. If a carve is interrupted, the same folder is
reused and PhotoRec is started with /cmd resume.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from filewinch.core import privilege

WHOLE = "wholespace"
FREE = "freespace"

PROGRESS_RE = re.compile(r"(?:Reading sector|sector)\s+(\d+)\s*/\s*(\d+)")


def options(mode: str, partition: int | None = None) -> str:
    """The /cmd option string.

    Whole source: partition_none treats the source as one big region, which is
    what you want when the filesystem is gone. Free space: pick the partition
    (PhotoRec's own numbering) and carve only its unallocated blocks.
    """
    head = ["partition_none"] if mode == WHOLE else ([str(partition)] if partition else [])
    return ",".join(head + ["options", "paranoid", "keep_corrupted_file_no",
                            "fileopt", "everything", "enable", mode, "search"])


def argv(source: str, out_prefix: Path, mode: str = WHOLE, partition: int | None = None,
         root: bool = False) -> list[str]:
    cmd = [shutil.which("photorec") or "photorec", "/log", "/d", str(out_prefix),
           "/cmd", source, options(mode, partition)]
    if root:
        cmd = privilege.as_root(["photorec", *cmd[1:]])
    return cmd


def resume_argv(out_prefix: Path, root: bool = False) -> list[str]:
    # PhotoRec's parser wants one argument after "resume"; it is ignored.
    cmd = [shutil.which("photorec") or "photorec", "/log", "/d", str(out_prefix),
           "/cmd", "resume", "search"]
    if root:
        cmd = privilege.as_root(["photorec", *cmd[1:]])
    return cmd


def has_session(session_dir: Path) -> bool:
    return (session_dir / "photorec.ses").exists()


def parse_progress(line: str) -> float | None:
    m = PROGRESS_RE.search(line)
    if m and int(m.group(2)):
        return int(m.group(1)) / int(m.group(2))
    return None


def carved_files(out_prefix: Path) -> list[Path]:
    """Every file PhotoRec wrote, across recup_dir.1, .2 and so on."""
    files = []
    parent = out_prefix.parent
    for d in sorted(parent.glob(out_prefix.name + ".*")):
        if d.is_dir():
            files.extend(p for p in sorted(d.iterdir())
                         if p.is_file() and p.name != "report.xml")
    return files
