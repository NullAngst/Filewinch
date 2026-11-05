"""MPEG-TS and M2TS (Blu-ray, AVCHD)."""

from __future__ import annotations

import re
from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, ffmpeg_base, media_findings, media_verify,
                                     reencode_recipes, split_recipe)

FAMILY = "MPEG-TS"
EXTS = {"ts", "m2ts", "mts", "tp", "trp"}


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    tool = ctx.tool("tsanalyze")
    if tool:
        res = ctx.run([tool, str(ctx.src)])
        m = re.search(r"Discontinuities\s*\.+\s*:?\s*([\d,]+)", res.stdout)
        if m and int(m.group(1).replace(",", "")):
            out.append(Finding("continuity", f"TSDuck counts {m.group(1)} continuity discontinuities.",
                               "tsanalyze"))
        m = re.search(r"Invalid sync\s*\.+\s*:?\s*([\d,]+)", res.stdout)
        if m and int(m.group(1).replace(",", "")):
            out.append(Finding("sync", f"{m.group(1)} packets lost sync."))
    out += media_findings(ctx)
    return out


def _remux_ts(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                                   "-i", str(c.src), "-map", "0", "-map", "-0:d", "-c", "copy",
                                   "-f", "mpegts", str(out)])]


def _remux_mkv(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                                   "-i", str(c.src), "-map", "0", "-map", "-0:d", "-c", "copy", str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    ext = "m2ts" if ctx.real_ext in ("m2ts", "mts") else "ts"
    return [
        Recipe("Remux dropping corrupt packets", ext, _remux_ts, needs=("ffmpeg",)),
        Recipe("Remux to MKV", "mkv", _remux_mkv, needs=("ffmpeg",)),
        split_recipe(),
        *reencode_recipes(ctx, "mkv"),
    ]


def verify(ctx: Ctx, out: Path):
    return media_verify(ctx, out)
