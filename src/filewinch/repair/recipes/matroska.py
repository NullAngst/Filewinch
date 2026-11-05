"""MKV and WebM."""

from __future__ import annotations

import json
from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, decode_check, ffmpeg_base,
                                     media_findings, media_verify, reencode_recipes,
                                     split_recipe)

FAMILY = "Matroska"
EXTS = {"mkv", "webm"}

CODEC_EXT = {
    "V_MPEG4/ISO/AVC": "h264", "V_MPEGH/ISO/HEVC": "h265", "V_MPEG2": "m2v", "V_VP8": "ivf",
    "V_VP9": "ivf", "V_AV1": "ivf", "A_AAC": "aac", "A_AC3": "ac3", "A_EAC3": "eac3", "A_DTS": "dts",
    "A_MPEG/L3": "mp3", "A_FLAC": "flac", "A_OPUS": "opus", "A_VORBIS": "ogg", "A_PCM/INT/LIT": "wav",
    "S_TEXT/UTF8": "srt", "S_TEXT/ASS": "ass", "S_TEXT/SSA": "ssa",
}


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    tool = ctx.tool("mkvinfo")
    if tool:
        res = ctx.run([tool, str(ctx.src)])
        text = res.stdout + "\n" + res.stderr
        if "Cues" not in text:
            out.append(Finding("no-cues", "The index (Cues) is missing, so seeking will not work."))
        if "Seek head" not in text:
            out.append(Finding("no-seekhead", "The SeekHead is missing or broken."))
        errs = [l for l in text.splitlines() if "Error" in l or "error" in l]
        if errs:
            out.append(Finding("structure", f"mkvinfo reports {len(errs)} structure problem(s).", errs[0].strip()))
    out += media_findings(ctx)
    return out


def _mkvmerge(c: Ctx, out: Path) -> list[Step]:
    # Exit 1 means warnings, which is expected on a damaged file.
    return [Step([c.tool("mkvmerge"), "-o", str(out), str(c.src)], ok_codes=(0, 1))]


def _ffmpeg(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src), "-map", "0",
                                   "-c", "copy", str(out)])]


def _extract_clean(c: Ctx, out: Path) -> bool:
    """Extract each track, keep the ones that decode clean, mux them back."""
    res = c.run([c.tool("mkvmerge"), "-J", str(c.src)], keep_stdout=True)
    try:
        tracks = json.loads(res.stdout).get("tracks", [])
    except ValueError:
        return False
    specs, files = [], []
    for t in tracks:
        cid = t.get("properties", {}).get("codec_id", "")
        ext = CODEC_EXT.get(cid)
        if not ext:
            continue
        f = c.work / f"track{t['id']}.{ext}"
        specs.append(f"{t['id']}:{f}")
        files.append((t, f))
    if not specs:
        return False
    c.run([c.tool("mkvextract"), str(c.src), "tracks", *specs])
    clean = []
    for t, f in files:
        if not f.exists() or f.stat().st_size == 0:
            continue
        if t.get("type") == "subtitles":
            clean.append(f)
            continue
        errors, _t, _d = decode_check(c, f)
        if errors == 0:
            clean.append(f)
        elif c.log:
            c.log(f"track {t['id']} ({t.get('codec')}) has {errors} decode errors, left out")
    if not clean:
        return False
    res = c.run([c.tool("mkvmerge"), "-o", str(out), *[str(f) for f in clean]])
    return res.code in (0, 1) and out.exists()


def recipes(ctx: Ctx) -> list[Recipe]:
    return [
        Recipe("mkvmerge rewrite (skips damaged clusters, rebuilds Cues and SeekHead)", "mkv", _mkvmerge,
               needs=("mkvmerge",)),
        Recipe("ffmpeg remux", "mkv", _ffmpeg, needs=("ffmpeg",)),
        Recipe("Extract tracks and remux the clean ones", "mkv", lambda c, o: [],
               needs=("mkvmerge", "mkvextract", "ffmpeg"), custom=_extract_clean),
        split_recipe(),
        *reencode_recipes(ctx, "mkv"),
    ]


def verify(ctx: Ctx, out: Path):
    return media_verify(ctx, out)
