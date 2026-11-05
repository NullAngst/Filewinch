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
TIME_RE = re.compile(r"^out_time_us=(\d+)")


def log_source(line: str) -> str:
    """Which component logged a line: "[h264 @ 0x...]" gives "h264".

    FFmpeg 7.1 and later prefix demuxers and muxers with their stream, as in
    "[in#0/matroska,webm @ 0x...]" and "[out#0/null @ 0x...]"; the prefix is
    dropped so both forms read the same.
    """
    if not line.startswith("[") or " @" not in line:
        return ""
    tag = line[1:line.find(" @")]
    return tag.split("/", 1)[1] if re.match(r"^(?:in|out)#\d+/", tag) else tag


def _has_video(info: dict) -> bool:
    return any(s.get("codec_type") == "video" for s in info.get("streams", []))


def decode_check(ctx: Ctx, path: Path, extra_input: list[str] | None = None) -> tuple[int, list[float], float]:
    """Decode every frame with ffmpeg and count errors.

    The universal check is ffmpeg -v error -i in -f null -, which decodes every
    frame and logs each error. To place each error in time, video goes through
    showinfo, which logs every frame's timestamp in the same stream as the
    errors; audio-only files use ffmpeg's progress output instead.
    Returns (error_count, error_times_seconds, decoded_seconds), times
    relative to the start of the file.
    """
    tool = ctx.tool("ffmpeg")
    if not tool:
        return -1, [], 0.0
    info = ffprobe_json(ctx, path)
    video = _has_video(info)
    if video:
        # One decoder thread, so each error line comes out right before the
        # frame it belongs to instead of wherever a worker thread got to.
        argv = [tool, "-nostdin", "-hide_banner", "-loglevel", "level+info", "-threads", "1",
                *(extra_input or []), "-i", str(path), "-map", "0:v:0", "-map", "0:a?",
                "-filter:v:0", "showinfo", "-f", "null", "-"]
    else:
        argv = [tool, "-nostdin", "-hide_banner", "-v", "error", *(extra_input or []), "-i", str(path),
                "-map", "0:a?", "-f", "null", "-progress", "pipe:2", "-stats_period", "0.05", "-"]
    res = run_command(argv, cancel=ctx.cancel)
    # The demuxer reads ahead of the decoder, so its errors are logged early.
    # Decoder errors place the damage; demuxer errors only when there are none.
    demuxers = set((info.get("format", {}).get("format_name") or "").split(",")) | {
        info.get("format", {}).get("format_name") or ""}
    first = None
    now = 0.0
    errors = 0
    pending = 0
    dec_times: list[float] = []
    demux_times: list[float] = []
    times: list[float] = []
    for line in res.stderr.splitlines():
        if video:
            m = LEVEL_RE.match(line)
            if not m:
                continue
            level = m.group(1)
            if level == "info":
                if "Parsed_showinfo" in line and "pts_time:" in line:
                    pm = PTS_RE.search(line)
                    if pm:
                        t = float(pm.group(1))
                        if first is None:
                            first = t
                        t -= first
                        if pending:
                            dec_times.extend([t] * pending)
                            pending = 0
                        now = max(now, t)
                continue
            if level not in ("error", "fatal", "panic"):
                continue
            source = log_source(line)
            # The null muxer complaining about output timestamps is not damage
            # in the file being checked.
            if source == "null":
                continue
            errors += 1
            if source in demuxers:
                demux_times.append(now)
            else:
                pending += 1
        else:
            m = TIME_RE.match(line)
            if m:
                now = int(m.group(1)) / 1_000_000
                continue
            if re.match(r"^[a-z0-9_]+=", line):
                continue  # other progress keys
            if line.strip() and log_source(line) != "null":
                errors += 1
                times.append(now)
    if video:
        dec_times.extend([now] * pending)
        times = dec_times or demux_times
    if res.code != 0 and errors == 0:
        errors = 1
    return errors, times, now


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
    return Verify(ok, errors, dur, detail)


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
