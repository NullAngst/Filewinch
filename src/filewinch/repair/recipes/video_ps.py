"""VOB, VRO and MPEG program streams."""

from __future__ import annotations

from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, ffmpeg_base, media_findings, media_verify,
                                     reencode_recipes, split_recipe)

FAMILY = "VOB / MPEG-PS"
EXTS = {"vob", "vro", "mpg", "m2v", "ps", "mpeg"}


def _timestamp_findings(ctx: Ctx) -> list[Finding]:
    """Missing or out-of-order timestamps on the video stream."""
    tool = ctx.tool("ffprobe")
    if not tool:
        return []
    res = ctx.run([tool, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,dts_time",
                   "-of", "csv=p=0", str(ctx.src)], keep_stdout=True)
    missing = backwards = jumps = 0
    last = None
    for line in res.stdout.splitlines():
        pts = line.split(",")[0].strip()
        if not pts or pts == "N/A":
            missing += 1
            continue
        try:
            t = float(pts)
        except ValueError:
            missing += 1
            continue
        if last is not None:
            if t < last - 1.0:
                backwards += 1
            elif t - last > 2.0:
                jumps += 1
        last = t
    out = []
    if missing:
        out.append(Finding("pts-missing", f"{missing} video packets have no timestamp."))
    if backwards or jumps:
        out.append(Finding("pts-order", f"Timestamps run backwards {backwards} times and jump forward {jumps} times.",
                           "SCR/PTS discontinuity"))
    return out


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    streams = ctx.info.get("streams", [])
    if streams:
        kinds = ", ".join(f"{s.get('codec_type')}:{s.get('codec_name')}" for s in streams)
        out.append(Finding("streams", f"Streams found: {kinds}."))
    out += media_findings(ctx)
    out += _timestamp_findings(ctx)
    return out


def _remux_mkv(c: Ctx, out: Path) -> list[Step]:
    # -map -0:d drops DVD navigation packets, which Matroska cannot hold.
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                                   "-i", str(c.src), "-map", "0", "-map", "-0:d", "-c", "copy", str(out)])]


def _remux_mpg(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-fflags", "+genpts+discardcorrupt", "-err_detect", "ignore_err",
                                   "-i", str(c.src), "-map", "0", "-map", "-0:d", "-c", "copy",
                                   "-f", "vob", str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    return [
        Recipe("Remux to MKV with regenerated timestamps", "mkv", _remux_mkv, needs=("ffmpeg",)),
        Recipe("Remux to MPEG-PS with regenerated timestamps", "mpg", _remux_mpg, needs=("ffmpeg",)),
        split_recipe(),
        *reencode_recipes(ctx, "mkv"),
    ]


def verify(ctx: Ctx, out: Path):
    return media_verify(ctx, out)
