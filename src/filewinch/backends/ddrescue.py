"""GNU ddrescue: image a drive or data disc to a file, with a mapfile so it resumes."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from filewinch.core import privilege

PCT_RE = re.compile(r"pct rescued:\s+([\d.]+)%")
OLD_RESCUED_RE = re.compile(r"rescued:\s+([\d.]+)\s*([kMGT]?B)")


def prepare_files(image: Path, mapfile: Path) -> None:
    """Create the image and mapfile as the user before root writes into them.

    ddrescue writes into existing files without recreating them, so they stay
    owned by the user and nothing after imaging needs root.
    """
    image.parent.mkdir(parents=True, exist_ok=True)
    image.touch(exist_ok=True)
    mapfile.touch(exist_ok=True)


def passes(source: str, image: Path, mapfile: Path, disc: bool = False,
           root: bool = True, retries: int = 3) -> list[list[str]]:
    """The passes to run in order. Each resumes from the mapfile.

    Pass 1 copies everything that reads easily and skips the rest (-n), so a
    dying drive gives up its good areas first. Pass 2 goes back for the bad
    areas with direct access and retries. Discs use 2048-byte sectors.
    """
    base = [shutil.which("ddrescue") or "ddrescue"]
    common = ["-b", "2048"] if disc else []
    is_device = source.startswith("/dev/")
    first = base + common + ["-n", source, str(image), str(mapfile)]
    second = base + common + (["-d"] if is_device else []) + [f"-r{retries}", source, str(image), str(mapfile)]
    out = [first, second]
    if root and is_device:
        out = [privilege.as_root(["ddrescue", *cmd[1:]]) for cmd in out]
    return out


def parse_progress(line: str) -> float | None:
    m = PCT_RE.search(line)
    if m:
        return float(m.group(1)) / 100.0
    return None


@dataclass
class MapSummary:
    size: int = 0
    rescued: int = 0
    bad: int = 0
    untried: int = 0

    @property
    def complete(self) -> bool:
        return self.size > 0 and self.rescued == self.size


def read_mapfile(mapfile: Path) -> MapSummary:
    """Totals from a ddrescue mapfile. Status '+' is good, '-' is bad sectors."""
    s = MapSummary()
    try:
        lines = mapfile.read_text(errors="replace").splitlines()
    except OSError:
        return s
    data = [l for l in lines if l.strip() and not l.lstrip().startswith("#")]
    for line in data[1:]:  # first data line is current position and status
        parts = line.split()
        if len(parts) < 3:
            continue
        try:
            size = int(parts[1], 0)
        except ValueError:
            continue
        s.size += size
        status = parts[2]
        if status == "+":
            s.rescued += size
        elif status == "-":
            s.bad += size
        else:
            s.untried += size
    return s
