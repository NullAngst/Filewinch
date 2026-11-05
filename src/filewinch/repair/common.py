"""Shared pieces for identify, diagnose and the recipes."""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from filewinch.core.jobs import CancelToken, run_command

ZERO_BLOCK = 4096


@dataclass
class Finding:
    kind: str          # short key, e.g. "mislabeled", "decode-errors", "zeros"
    text: str          # plain words for the findings list
    evidence: str = "" # the line or numbers it came from


@dataclass
class Step:
    argv: list[str]
    stdout_file: str = ""
    stdin_file: str = ""
    cwd: str = ""
    ok_codes: tuple[int, ...] = (0,)


@dataclass
class Recipe:
    name: str
    out_ext: str
    build: Callable[["Ctx", Path], list[Step]]
    needs: tuple[str, ...] = ()
    lossless: bool = True
    reencode: bool = False
    needs_reference: bool = False
    note: str = ""
    # For recipes with checks between steps: runs itself, returns True when
    # it produced the output file. build is ignored when this is set.
    custom: Callable[["Ctx", Path], bool] | None = None


@dataclass
class Verify:
    ok: bool
    errors: int = 0
    duration: float = 0.0
    detail: str = ""
    lost: bool = False   # the output is missing a stream the original had


@dataclass
class Ctx:
    src: Path
    work: Path
    tools: object                 # core.deps.Report, or anything with .path(key)
    log: Callable[[str], None] | None = None
    cancel: CancelToken | None = None
    lossy_codec: str = "h265"
    reference: Path | None = None
    info: dict = field(default_factory=dict)       # ffprobe json of the source
    findings: list[Finding] = field(default_factory=list)
    damaged: list[tuple[float, float]] = field(default_factory=list)  # seconds
    real_ext: str = ""

    def tool(self, key: str) -> str:
        p = ""
        if hasattr(self.tools, "path"):
            p = self.tools.path(key)
        return p or shutil.which(key) or ""

    def have(self, key: str) -> bool:
        return bool(self.tool(key))

    def run(self, argv, **kw):
        return run_command(argv, log=self.log, cancel=self.cancel, **kw)


def ffprobe_json(ctx: Ctx, path: Path) -> dict:
    tool = ctx.tool("ffprobe")
    if not tool:
        return {}
    res = run_command([tool, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
                      cancel=ctx.cancel, timeout=120)
    try:
        return json.loads(res.stdout or "{}")
    except ValueError:
        return {}


def duration_of(info: dict) -> float:
    try:
        d = float(info.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        d = 0.0
    if d:
        return d
    best = 0.0
    for s in info.get("streams", []):
        try:
            best = max(best, float(s.get("duration", 0) or 0))
        except (TypeError, ValueError):
            pass
    return best


LEVEL_RE = re.compile(r"^\[(?:[^\]]*\] )?\[(fatal|error|warning|info|panic)\]")
PTS_RE = re.compile(r"pts_time:\s*(-?[\d.]+)")


def log_source(line: str) -> str:
    """Which component logged a line: "[h264 @ 0x...]" gives "h264".

    FFmpeg 7.1 and later prefix components with their file or stream, as in
    "[in#0/matroska,webm @ 0x...]", "[vist#0:0/h264 @ 0x...]" and
    "[out#0/null @ 0x...]"; the prefix is dropped so every version reads the same.
    """
    if not line.startswith("[") or " @" not in line:
        return ""
    tag = line[1:line.find(" @")]
    m = re.match(r"^[a-z]+#\d+(?::\d+)?/(.*)$", tag)
    tag = m.group(1) if m else tag
    return re.sub(r"^(?:dec|enc):", "", tag)


# FFmpeg 8.0's Matroska demuxer mangles the final Opus packet of perfectly
# healthy files, and both Opus decoders then report "Error parsing Opus packet
# header". Audio decoder errors in the last second of the stream are ignored.
# A second, because ffmpeg 8 buffers audio frames ahead of the filter that
# timestamps them, so the error is logged up to about half a second early.
# The cost: real damage in the final second of a soundtrack goes unreported.
AUDIO_TAIL_GRACE = 1.0


def _decode_pass(ctx: Ctx, path: Path, kind: str, extra_input: list[str], demuxers: set[str],
                 others: set[str] | None = None) -> dict:
    """Decode one kind of stream and place each error in time.

    One decoder thread, and showinfo (video) or ashowinfo (audio) logging every
    frame's timestamp in the same stream as the errors, so each error line
    comes out right before the frame it belongs to. Audio gets its own pass:
    newer ffmpeg decodes audio on another thread, so placing audio errors by
    video frame times put them anywhere.

    others: codec names of the streams this pass does not decode. ffmpeg 8
    runs packet parsers (Opus, for one) for every stream inside the demuxer,
    so their errors show up in every pass and belong to the other one.
    """
    others = others or set()
    tool = ctx.tool("ffmpeg")
    if kind == "video":
        sel = ["-map", "0:v:0", "-filter:v:0", "showinfo"]
        tag = "Parsed_showinfo"
    else:
        sel = ["-map", "0:a", "-filter:a", "ashowinfo"]
        tag = "Parsed_ashowinfo"
    argv = [tool, "-nostdin", "-hide_banner", "-loglevel", "level+info", "-threads", "1",
            *extra_input, "-i", str(path), *sel, "-f", "null", "-"]
    res = run_command(argv, cancel=ctx.cancel)
    out = {"dec": [], "demux": [], "now": 0.0, "code": res.code}
    first = None
    pending = 0
    for line in res.stderr.splitlines():
        m = LEVEL_RE.match(line)
        if not m:
            continue
        level = m.group(1)
        if level == "info":
            if tag in line and "pts_time:" in line:
                pm = PTS_RE.search(line)
                if pm:
                    t = float(pm.group(1))
                    if first is None:
                        first = t
                    t -= first
                    if pending:
                        out["dec"].extend([t] * pending)
                        pending = 0
                    out["now"] = max(out["now"], t)
            continue
        if level not in ("error", "fatal", "panic"):
            continue
        source = log_source(line)
        # The null muxer complaining about output timestamps is not damage in
        # the file being checked.
        if source == "null":
            continue
        if source in others or (source.startswith("lib") and source[3:] in others):
            continue
        if source in demuxers:
            out["demux"].append(out["now"])
        else:
            pending += 1
    out["dec"].extend([out["now"]] * pending)
    return out


def decode_check(ctx: Ctx, path: Path, extra_input: list[str] | None = None) -> tuple[int, list[float], float]:
    """Decode every frame with ffmpeg and count errors.

    The universal check is ffmpeg -v error -i in -f null -, which decodes every
    frame and logs each error. Here it runs once for the first video stream and
    once for the audio, so each error can be placed in time.
    Returns (error_count, error_times_seconds, decoded_seconds), times
    relative to the start of the file.
    """
    if not ctx.tool("ffmpeg"):
        return -1, [], 0.0
    info = ffprobe_json(ctx, path)
    streams = info.get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    names = {k: {s.get("codec_name") for s in streams if s.get("codec_type") == k} - {None}
             for k in ("video", "audio")}
    fmt = info.get("format", {}).get("format_name") or ""
    # The demuxer reads ahead of the decoder, so its errors are logged early.
    # Decoder errors place the damage; demuxer errors only when there are none.
    demuxers = set(fmt.split(",")) | {fmt}
    extra = list(extra_input or [])
    errors = 0
    times: list[float] = []
    demux_times: list[float] = []
    decoded = 0.0
    passes = 0
    if "video" in kinds:
        v = _decode_pass(ctx, path, "video", extra, demuxers, names["audio"])
        passes += 1
        errors += len(v["dec"]) + len(v["demux"])
        times += v["dec"]
        demux_times += v["demux"]
        decoded = v["now"]
        if v["code"] != 0 and not v["dec"] and not v["demux"]:
            errors += 1
    if "audio" in kinds:
        a = _decode_pass(ctx, path, "audio", extra, demuxers, names["video"])
        passes += 1
        real = [t for t in a["dec"] if t < a["now"] - AUDIO_TAIL_GRACE]
        ignored = len(a["dec"]) - len(real)
        errors += len(real)
        times += real
        if "video" not in kinds:
            # Demuxer errors were already counted by the video pass when there is one.
            errors += len(a["demux"])
            demux_times += a["demux"]
            decoded = a["now"]
        if a["code"] != 0 and not real and not a["demux"] and not ignored:
            errors += 1
    if not passes:
        res = run_command([ctx.tool("ffmpeg"), "-nostdin", "-v", "error", *extra, "-i", str(path),
                           "-f", "null", "-"], cancel=ctx.cancel)
        lines = [l for l in res.stderr.splitlines() if l.strip() and log_source(l) != "null"]
        errors = len(lines) or (1 if res.code != 0 else 0)
    return errors, sorted(times) or sorted(demux_times), decoded


def keyframes(ctx: Ctx, path: Path) -> list[float]:
    """Keyframe times of the first video stream, relative to its first packet."""
    tool = ctx.tool("ffprobe")
    res = run_command([tool, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time,flags",
                       "-of", "csv=p=0", str(path)], cancel=ctx.cancel)
    first = None
    out = []
    for line in res.stdout.splitlines():
        parts = line.split(",")
        if len(parts) < 2 or parts[0] in ("", "N/A"):
            continue
        try:
            t = float(parts[0])
        except ValueError:
            continue
        if first is None:
            first = t
        if "K" in parts[1]:
            out.append(t - first)
    return sorted(set(out))


def split_around_damage(c: Ctx, out: Path) -> bool:
    """Drop only the GOPs that hold the damage and join the rest, losslessly.

    Each damaged range loses everything from the keyframe at or before it to
    the first keyframe after it, so the pieces left all start on a keyframe
    and decode clean.
    """
    if not c.damaged:
        return False
    kfs = keyframes(c, c.src)
    total = duration_of(c.info)
    if not kfs or not total:
        return False
    drop: list[tuple[float, float]] = []
    for a, b in c.damaged:
        before = [k for k in kfs if k <= a]
        after = [k for k in kfs if k > b]
        drop.append((before[-1] if before else 0.0, after[0] if after else total + 1))
    # Each kept piece ends just before the keyframe that starts the damaged GOP.
    good: list[tuple[float, float]] = []
    pos = 0.0
    for a, b in sorted(drop):
        if a > pos:
            good.append((pos, a))
        pos = max(pos, b)
    if pos < total:
        good.append((pos, total + 1))
    if not good:
        return False
    # ffmpeg resyncs inside a damaged Matroska cluster where mkvmerge drops the
    # whole cluster, so ffmpeg does the cutting. Seeking is on the output side:
    # files with a broken index cannot be seeked on the input side without
    # landing back inside the damaged GOP. Cuts sit 1 ms before each keyframe,
    # since keyframe timestamps round to just under the printed value.
    parts = []
    for i, (a, b) in enumerate(good):
        part = c.work / f"part{i:03d}.mkv"
        cut = ["-ss", f"{max(0.0, a - 0.001):.3f}"] if a > 0 else []
        cut += ["-to", f"{b - 0.001:.3f}"] if b <= total else []
        res = c.run(ffmpeg_base(c) + ["-fflags", "+genpts+discardcorrupt", "-i", str(c.src), "-map", "0",
                                      "-map", "-0:d", "-c", "copy", *cut, "-avoid_negative_ts", "make_zero",
                                      str(part)])
        if part.exists() and part.stat().st_size:
            parts.append(part)
        elif c.log:
            c.log(f"piece {i} came out empty (exit {res.code})")
    if not parts:
        return False
    listing = c.work / "concat.txt"
    listing.write_text("".join(f"file '{p.name}'\n" for p in parts))
    res = c.run(ffmpeg_base(c) + ["-f", "concat", "-safe", "0", "-i", str(listing), "-map", "0",
                                  "-c", "copy", str(out)], cwd=str(c.work))
    ok = res.ok and out.exists()
    if c.log:
        c.log("Kept " + ", ".join(f"{hms(a)}-{hms(min(b, total))}" for a, b in good))
    return ok


def split_recipe() -> "Recipe":
    return Recipe("Cut out the damaged GOPs and join the rest", "mkv", lambda c, o: [], needs=("ffmpeg", "ffprobe"),
                  custom=split_around_damage)


def ranges(times: list[float], gap: float = 5.0) -> list[tuple[float, float]]:
    """Group error times into ranges."""
    out: list[tuple[float, float]] = []
    for t in sorted(times):
        if out and t - out[-1][1] <= gap:
            out[-1] = (out[-1][0], t)
        else:
            out.append((t, t))
    return out


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def size_text(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n} bytes"


def zero_runs(path: Path, block: int = ZERO_BLOCK) -> list[tuple[int, int]]:
    """Regions of block size or more that are all zeros, aligned to block.

    These usually mean TRIM, or a carve that grabbed empty space.
    """
    runs: list[tuple[int, int]] = []
    zero = bytes(block)
    start = None
    pos = 0
    with open(path, "rb") as fh:
        while True:
            chunk = fh.read(block)
            if not chunk:
                break
            if len(chunk) == block and chunk == zero:
                if start is None:
                    start = pos
            elif start is not None:
                runs.append((start, pos - start))
                start = None
            pos += len(chunk)
    if start is not None:
        runs.append((start, pos - start))
    return runs


def media_verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    info = ffprobe_json(ctx, out)
    dur = duration_of(info)
    errors, _times, decoded = decode_check(ctx, out)
    dur = max(dur, decoded) if dur == 0 else dur
    kinds_in = {s.get("codec_type") for s in ctx.info.get("streams", [])} & {"video", "audio"}
    kinds_out = {s.get("codec_type") for s in info.get("streams", [])} & {"video", "audio"}
    lost = kinds_in - kinds_out
    ok = errors == 0 and bool(kinds_out) and not lost
    detail = f"{errors} decode errors, {hms(dur)} long"
    if lost:
        detail += ", lost the " + " and ".join(sorted(lost)) + " stream"
    return Verify(ok, errors, dur, detail, lost=bool(lost))


def ffmpeg_base(ctx: Ctx) -> list[str]:
    return [ctx.tool("ffmpeg"), "-nostdin", "-hide_banner", "-y", "-v", "error"]


def reencode_recipes(ctx: Ctx, out_ext: str = "mkv") -> list[Recipe]:
    """The last resort, both choices. The engine asks before running either.

    -err_detect ignore_err plus ffmpeg's default error concealment patches
    damaged frames from their neighbours instead of dropping them.
    """
    def lossless(c: Ctx, out: Path) -> list[Step]:
        return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src),
                                       "-map", "0:v?", "-map", "0:a?",
                                       "-c:v", "ffv1", "-level", "3", "-slicecrc", "1",
                                       "-c:a", "copy", str(out)])]

    def lossy(c: Ctx, out: Path) -> list[Step]:
        if c.lossy_codec == "h264":
            v = ["-c:v", "libx264", "-crf", "18", "-preset", "veryfast"]
        else:
            v = ["-c:v", "libx265", "-crf", "22", "-preset", "fast"]
        return [Step(ffmpeg_base(c) + ["-err_detect", "ignore_err", "-i", str(c.src),
                                       "-map", "0:v?", "-map", "0:a?", *v, "-c:a", "copy", str(out)])]

    return [
        Recipe("Re-encode, lossless FFV1", out_ext, lossless, needs=("ffmpeg",), lossless=False, reencode=True,
               note="Every pixel ffmpeg could decode. Expect several times the original size."),
        Recipe("Re-encode, lossy", out_ext, lossy, needs=("ffmpeg",), lossless=False, reencode=True,
               note="Watchable at a sane size."),
    ]


def estimate_reencode_sizes(ctx: Ctx) -> dict[str, int]:
    """Rough output sizes for the re-encode question."""
    vs = [s for s in ctx.info.get("streams", []) if s.get("codec_type") == "video"]
    dur = duration_of(ctx.info) or 0
    if not vs or not dur:
        size = ctx.src.stat().st_size
        return {"ffv1": size * 4, "lossy": size}
    w = int(vs[0].get("width") or 720)
    h = int(vs[0].get("height") or 480)
    try:
        num, den = (vs[0].get("avg_frame_rate") or "30/1").split("/")
        fps = float(num) / float(den or 1) or 30.0
    except (ValueError, ZeroDivisionError):
        fps = 30.0
    # FFV1 on video content runs around 0.45 bytes per pixel per frame.
    ffv1 = int(w * h * fps * dur * 0.45)
    # Bits per pixel at the CRF settings used: rough, content-dependent.
    bits_pp = 0.05 if ctx.lossy_codec == "h265" else 0.1
    lossy = int(w * h * fps * dur * bits_pp / 8)
    return {"ffv1": ffv1, "lossy": lossy}


def media_findings(ctx: Ctx) -> list[Finding]:
    """Decode check plus the time ranges of the errors. Fills ctx.damaged."""
    from filewinch.repair.diagnose import decode_summary
    out = []
    errors, times, decoded = decode_check(ctx, ctx.src)
    if errors < 0:
        return [Finding("no-ffmpeg", "ffmpeg is missing, so the decode check could not run.")]
    rngs = ranges(times)
    ctx.damaged = rngs
    f = decode_summary(errors, rngs)
    if f:
        out.append(f)
    dur = duration_of(ctx.info)
    if dur and decoded and decoded < dur * 0.98:
        out.append(Finding("short-decode",
                           f"Only {hms(decoded)} of {hms(dur)} decodes before ffmpeg gives up or runs out of data."))
    if not ctx.info.get("streams"):
        out.append(Finding("no-streams", "ffprobe finds no audio or video streams."))
    return out
