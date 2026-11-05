"""MP4, MOV, M4V, 3GP."""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, ffmpeg_base, media_findings, media_verify,
                                     reencode_recipes, size_text, split_recipe)

FAMILY = "MP4 / MOV"
EXTS = {"mp4", "mov", "m4v", "3gp", "m4a"}

ATOM_RE = re.compile(r"type:'(.{4})' parent:'root' sz: (\d+) (\d+) (\d+)")


def atoms(ctx: Ctx) -> list[tuple[str, int, int]]:
    """Top-level atoms (type, start, size) from ffprobe -v trace."""
    tool = ctx.tool("ffprobe")
    if not tool:
        return []
    res = ctx.run([tool, "-v", "trace", "-i", str(ctx.src)], keep_stdout=False, timeout=300)
    out = []
    seen = set()
    for m in ATOM_RE.finditer(res.stderr):
        size, pos = int(m.group(2)), int(m.group(3))
        start = pos - 8 if pos >= 8 else pos
        # ffprobe can walk the top level twice; keep each atom once.
        if (m.group(1), start) in seen:
            continue
        seen.add((m.group(1), start))
        out.append((m.group(1), start, size))
    return out


def has_moov(ctx: Ctx) -> bool:
    return any(a[0] == "moov" for a in atoms(ctx))


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    found = atoms(ctx)
    real = ctx.src.stat().st_size
    types = [a[0] for a in found]
    if found:
        out.append(Finding("atoms", "Top-level atoms: " + ", ".join(types) + "."))
    if "moov" not in types:
        out.append(Finding("no-moov", "The moov atom (the index) is missing. Nothing can play this until it is rebuilt."))
    for t, start, size in found:
        if t == "mdat" and start + size > real:
            out.append(Finding("truncated",
                               f"The file ends {size_text(start + size - real)} short of its declared size.",
                               f"mdat at {start} declares {size} bytes, file is {real}"))
    if "moov" in types:
        out += media_findings(ctx)
    return out


def _remux(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src),
                                   "-map", "0:v?", "-map", "0:a?", "-map", "0:s?", "-c", "copy",
                                   "-movflags", "+faststart", str(out)])]


def _untrunc(c: Ctx, out: Path, skip: bool = False) -> bool:
    """untrunc needs a good file from the same camera or app, same settings.

    skip=True adds -s, which steps over byte sequences untrunc cannot match to
    a codec instead of stopping there.
    """
    if not c.reference:
        return False
    # untrunc writes <input>_fixed... next to its input. A symlink in the work
    # folder keeps that output out of the original's folder.
    link = c.work / ("broken" + c.src.suffix.lower())
    if link.exists() or link.is_symlink():
        link.unlink()
    os.symlink(c.src, link)
    for old in c.work.glob("broken*_fixed*"):
        old.unlink()
    res = c.run([c.tool("untrunc"), *(["-s"] if skip else []), str(c.reference), str(link)], cwd=str(c.work))
    fixed = sorted(c.work.glob("broken*_fixed*"))
    if not fixed:
        return False
    shutil.move(str(fixed[0]), str(out))
    link.unlink()
    return out.exists() and out.stat().st_size > 0 and res.code in (0, 1)


def _mp4box(c: Ctx, out: Path) -> list[Step]:
    return [Step([c.tool("MP4Box"), "-add", str(c.src), "-new", str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    chain = []
    if has_moov(ctx):
        chain.append(Recipe("Remux with the existing index", ctx.real_ext if ctx.real_ext in ("mov", "mp4") else "mp4",
                            _remux, needs=("ffmpeg",)))
    note = "Needs a good file from the same camera or app, recorded with the same settings."
    chain.append(Recipe("untrunc against a reference file", "mp4", lambda c, o: [], needs=("untrunc",),
                        needs_reference=True, custom=_untrunc, note=note))
    chain.append(Recipe("untrunc, stepping over unknown data (-s)", "mp4", lambda c, o: [], needs=("untrunc",),
                        needs_reference=True, custom=lambda c, o: _untrunc(c, o, skip=True), note=note))
    chain.append(Recipe("MP4Box index rebuild", "mp4", _mp4box, needs=("MP4Box",)))
    chain.append(split_recipe())
    chain.extend(reencode_recipes(ctx, "mkv"))
    return chain


def verify(ctx: Ctx, out: Path):
    v = media_verify(ctx, out)
    # untrunc can "succeed" with a few frames. Without the original's index there
    # is no true length, so the mdat size against the output size is the check:
    # an output that kept under half the media data has not recovered the file.
    if v.ok and not has_moov(ctx):
        mdat = sum(a[2] for a in atoms(ctx) if a[0] == "mdat")
        if mdat and out.stat().st_size < mdat * 0.5:
            v.ok = False
            v.detail += f", only {out.stat().st_size * 100 // max(mdat, 1)}% of the media data recovered"
    return v
