"""cdparanoia: audio CD rescue with full paranoia error correction."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from filewinch.core import privilege

PROG_RE = re.compile(r"outputting to (\S+)")


def argv(device: str, root: bool = True) -> list[str]:
    """One WAV per track into the working directory, plus a summary log.

    -B batch mode writes trackNN.cdda.wav. -L writes cdparanoia.log with a
    summary of every sector that needed correction or could not be read.
    """
    cmd = [shutil.which("cdparanoia") or "cdparanoia", "-d", device, "-B", "-L", "-w"]
    if root:
        cmd = privilege.as_root(["cdparanoia", *cmd[1:]])
    return cmd


def report(job_dir: Path) -> str:
    log = job_dir / "cdparanoia.log"
    try:
        return log.read_text(errors="replace")
    except OSError:
        return ""


def tracks(job_dir: Path) -> list[Path]:
    return sorted(job_dir.glob("track*.wav"))
