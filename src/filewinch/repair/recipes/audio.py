"""MP3, FLAC, WAV and AIFF."""

from __future__ import annotations

import shutil
import struct
from pathlib import Path

from filewinch.repair.common import (Ctx, Finding, Recipe, Step, Verify, decode_check, duration_of, ffmpeg_base,
                                     ffprobe_json, media_findings, size_text)

FAMILY = "Audio"
EXTS = {"mp3", "flac", "wav", "aiff"}


def _riff_declared(path: Path) -> tuple[str, int] | None:
    with open(path, "rb") as fh:
        head = fh.read(12)
    if len(head) < 12:
        return None
    if head[:4] == b"RIFF":
        return "RIFF", struct.unpack("<I", head[4:8])[0] + 8
    if head[:4] == b"FORM":
        return "FORM", struct.unpack(">I", head[4:8])[0] + 8
    return None


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    ext = ctx.real_ext
    if ext == "mp3" and ctx.have("mp3val"):
        res = ctx.run([ctx.tool("mp3val"), str(ctx.src)])
        warns = [l for l in res.stdout.splitlines() if l.startswith("WARNING")]
        if warns:
            out.append(Finding("frames", f"mp3val reports {len(warns)} problem(s).", warns[0]))
    elif ext == "flac" and ctx.have("flac"):
        res = ctx.run([ctx.tool("flac"), "-t", "-s", str(ctx.src)])
        if res.code != 0:
            out.append(Finding("flac-test", "flac -t fails: the stream has errors.",
                               (res.stderr.strip().splitlines() or [""])[-1]))
    elif ext in ("wav", "aiff"):
        decl = _riff_declared(ctx.src)
        real = ctx.src.stat().st_size
        if decl and decl[1] != real:
            out.append(Finding("header-size",
                               f"The header says {size_text(decl[1])} but the file is {size_text(real)}.",
                               f"{decl[0]} size field"))
    out += media_findings(ctx)
    return out


def _mp3val(c: Ctx, out: Path) -> bool:
    # mp3val fixes in place, so it works on a copy.
    shutil.copyfile(c.src, out)
    res = c.run([c.tool("mp3val"), "-f", "-nb", str(out)])
    return res.ok and out.exists()


def _remux_mp3(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src), "-map", "0:a", "-c", "copy",
                                   str(out)])]


def _flac(c: Ctx, out: Path) -> list[Step]:
    wav = c.work / "decoded.wav"
    return [Step([c.tool("flac"), "-d", "-F", "-f", "-o", str(wav), str(c.src)], ok_codes=(0, 1)),
            Step([c.tool("flac"), "-f", "-o", str(out), str(wav)])]


def _flac_ffmpeg(c: Ctx, out: Path) -> list[Step]:
    return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src), "-map", "0:a",
                                   "-c:a", "flac", str(out)])]


def _pcm(c: Ctx, out: Path) -> list[Step]:
    extra = ["-ignore_length", "1"] if c.real_ext == "wav" else []
    return [Step(ffmpeg_base(c) + extra + ["-i", str(c.src), "-map", "0:a", "-c", "copy", str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    ext = ctx.real_ext
    if ext == "mp3":
        return [Recipe("mp3val frame repair", "mp3", lambda c, o: [], needs=("mp3val",), custom=_mp3val),
                Recipe("ffmpeg remux", "mp3", _remux_mp3, needs=("ffmpeg",))]
    if ext == "flac":
        return [Recipe("Decode through errors and re-encode to FLAC (lossless both ways)", "flac", _flac,
                       needs=("flac",)),
                Recipe("ffmpeg decode to FLAC (lossless)", "flac", _flac_ffmpeg, needs=("ffmpeg",))]
    return [Recipe("Rewrite with a corrected header", ext, _pcm, needs=("ffmpeg",))]


def verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    if ctx.real_ext == "flac" and ctx.have("flac"):
        res = ctx.run([ctx.tool("flac"), "-t", "-s", str(out)])
        if res.code != 0:
            return Verify(False, 1, detail="flac -t fails")
    if ctx.real_ext in ("wav", "aiff"):
        decl = _riff_declared(out)
        if decl and decl[1] != out.stat().st_size:
            return Verify(False, 1, detail="header still wrong")
    errors, _t, decoded = decode_check(ctx, out)
    dur = duration_of(ffprobe_json(ctx, out)) or decoded
    return Verify(errors == 0, errors, dur, f"{errors} decode errors")
