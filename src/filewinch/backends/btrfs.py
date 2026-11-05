"""btrfs restore from an older tree root, on a partition image."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from filewinch.core.jobs import run_command

ROOT_RE = re.compile(r"Well block (\d+)\(gen: (\d+)")


def older_roots(image: str, log=None) -> list[tuple[int, int]]:
    """(bytenr, generation) pairs from btrfs-find-root, newest first."""
    tool = shutil.which("btrfs-find-root")
    if not tool:
        return []
    res = run_command([tool, image], log=log, timeout=600)
    roots = [(int(b), int(g)) for b, g in ROOT_RE.findall(res.stdout + "\n" + res.stderr)]
    return sorted(set(roots), key=lambda r: -r[1])


def restore_argv(image: str, dest: Path, bytenr: int | None = None) -> list[str]:
    tool = shutil.which("btrfs") or "btrfs"
    cmd = [tool, "restore", "-i", "-v"]
    if bytenr:
        cmd += ["-t", str(bytenr)]
    return cmd + [image, str(dest)]
