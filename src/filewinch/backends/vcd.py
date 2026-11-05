"""vcdxrip: pull MPEG tracks off a Video CD or SVCD.

VCD video lives in Mode 2 Form 2 sectors that a plain 2048-byte read cannot
see, which is why ddrescue is not used for these.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from filewinch.core import privilege


def argv(device: str, root: bool = True) -> list[str]:
    cmd = [shutil.which("vcdxrip") or "vcdxrip", f"--cdrom-device={device}", "--progress"]
    if root:
        cmd = privilege.as_root(["vcdxrip", *cmd[1:]])
    return cmd


def tracks(job_dir: Path) -> list[Path]:
    return sorted(p for p in job_dir.iterdir() if p.suffix.lower() in (".mpg", ".mpeg", ".dat"))
