"""AVI."""

from __future__ import annotations

import struct
from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, ffmpeg_base, media_findings, media_verify,
                                     reencode_recipes, split_recipe)

FAMILY = "AVI"
EXTS = {"avi"}


def riff_chunks(path: Path) -> list[tuple[str, int]]:
    """Top-level chunks of the first RIFF list, enough to see whether idx1 exists."""
    out = []
    with open(path, "rb") as fh:
        head = fh.read(12)
        if len(head) < 12 or head[:4] != b"RIFF":
            return out
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            cid, size = hdr[:4].decode("latin-1"), struct.unpack("<I", hdr[4:])[0]
            out.append((cid, size))
            fh.seek(size + (size & 1), 1)
    return out


def has_idx1(path: Path) -> bool:
    return any(c == "idx1" for c, _s in riff_chunks(path))


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    if not has_idx1(ctx.src):
        out.append(Finding("no-index", "The idx1 index is missing, so seeking will not work."))
    out += media_findings(ctx)
    return out


def _remux_avi(c: Ctx, out: Path) -> list[Step]:
    # ffmpeg's AVI muxer writes a fresh idx1.
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts", "-err_detect", "ignore_err", "-i", str(c.src),
                                   "-map", "0", "-c", "copy", str(out)])]


def _remux_mkv(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts", "-err_detect", "ignore_err", "-i", str(c.src),
                                   "-map", "0", "-c", "copy", str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    return [
        Recipe("Remux to AVI (rebuilds the index)", "avi", _remux_avi, needs=("ffmpeg",)),
        Recipe("Remux to MKV", "mkv", _remux_mkv, needs=("ffmpeg",)),
        split_recipe(),
        *reencode_recipes(ctx, "mkv"),
    ]


def verify(ctx: Ctx, out: Path):
    v = media_verify(ctx, out)
    if v.ok and out.suffix == ".avi" and not has_idx1(out):
        v.ok = False
        v.detail += ", still no index"
    return v
