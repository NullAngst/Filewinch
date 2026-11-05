"""ntfsundelete: a second opinion on NTFS, for partition images (offset 0)."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from filewinch.core.jobs import run_command

ROW_RE = re.compile(r"^\s*(\d+)\s+\S+\s+(\d+)%\s+\S+\s+\S+\s+(\d+)\s+(.+)$")


@dataclass
class NtfsEntry:
    inode: int
    percent: int
    size: int
    name: str


def scan(image: str, log=None, cancel=None) -> list[NtfsEntry]:
    tool = shutil.which("ntfsundelete")
    if not tool:
        return []
    res = run_command([tool, "--scan", "--force", image], log=log, cancel=cancel)
    out = []
    for line in res.stdout.splitlines():
        m = ROW_RE.match(line)
        if m:
            out.append(NtfsEntry(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).strip()))
    return out


def recover_argv(image: str, inode: int, dest_dir: Path) -> list[str]:
    tool = shutil.which("ntfsundelete") or "ntfsundelete"
    return [tool, "--undelete", "--force", "--inodes", str(inode), "--destination", str(dest_dir), image]
