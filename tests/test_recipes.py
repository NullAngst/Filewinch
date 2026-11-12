"""Repair acceptance tests, on files built by make-fixtures.sh."""

from __future__ import annotations

import shutil
import struct
import subprocess
import tempfile
import zipfile
from pathlib import Path

import pytest
from conftest import need, need_codecs, need_decoders_for

from filewinch.repair import engine, identify
from filewinch.repair import recipes as recipe_pkg
from filewinch.repair.common import Ctx, decode_check, duration_of, ffprobe_json


def ctx_for(src: Path, tools, work: Path, **kw) -> Ctx:
    ctx = Ctx(src=src, work=work, tools=tools, **kw)
    ident = identify.identify(ctx, src)
    ctx.real_ext = ident.real
    ctx.info = ident.info
    return ctx


def run_recipe(ctx: Ctx, name_part: str, out: Path) -> Path:
    """Run one named recipe from the file's chain and return its output."""
    module = recipe_pkg.for_ext(ctx.real_ext)
    if hasattr(module, "diagnose"):
        module.diagnose(ctx)
    recipe = next(r for r in module.recipes(ctx) if name_part.lower() in r.name.lower())
    attempt = engine.Attempt(recipe=recipe.name, ran=True)
    assert engine._run_recipe(ctx, recipe, out, attempt), f"{recipe.name} produced nothing"
    return out


def duration(ctx: Ctx, path: Path) -> float:
    return duration_of(ffprobe_json(ctx, path))


def video_codec(path: Path) -> str:
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=codec_name", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return probe.stdout.strip()


def test_mp4_without_moov_untrunc(fixtures, tools, tmp_path):
    need("ffmpeg")
    if not tools.available("untrunc"):
        pytest.skip("untrunc not built (packaging/build-untrunc.sh)")
    src = fixtures / "repair" / "no_moov.mp4"
    ref = fixtures / "repair" / "reference.mp4"
    need_decoders_for(ref)
    # untrunc walks H.264 and HEVC frame by frame. MPEG-4 Part 2, which
    # make-fixtures.sh falls back to where ffmpeg has no libx264 (stock Fedora
    # and openSUSE), stops it after a few frames, so there is nothing to test.
    video = video_codec(ref)
    if video not in ("h264", "hevc"):
        pytest.skip(f"fixture video is {video or 'missing'}: untrunc only rebuilds H.264 and HEVC reliably")
    outcome = engine.repair(src, tmp_path, tools, reference=ref)
    assert outcome.output, outcome.message
    assert "untrunc" in outcome.winner
    ctx = ctx_for(src, tools, tmp_path)
    original = duration(ctx, ref)  # the cut-off file was recorded the same length
    assert duration(ctx, outcome.output) >= 0.9 * original


def test_mp4_partial_untrunc_output_ranks_as_lost(fixtures, tools, tmp_path):
    """A few clean frames out of a moov-less MP4 must lose to a full-length
    output with errors. Runs on every distro: no untrunc, no H.264 needed."""
    need("ffmpeg", "ffprobe")
    src = fixtures / "repair" / "no_moov.mp4"
    ref = fixtures / "repair" / "reference.mp4"
    need_decoders_for(ref)
    short = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(ref), "-t", "0.2", "-c", "copy",
                    str(short)], check=True)
    ctx = ctx_for(src, tools, tmp_path)
    module = recipe_pkg.for_ext("mp4")
    v_short = module.verify(ctx, short)
    assert v_short.lost and not v_short.ok, v_short.detail
    v_full = module.verify(ctx, ref)
    assert not v_full.lost, v_full.detail
    full_with_errors = engine.Attempt("full", True, engine.Verify(False, 140, v_full.duration, "", lost=False))
    clean_but_short = engine.Attempt("short", True, v_short)
    assert min([clean_but_short, full_with_errors], key=engine._score) is full_with_errors


def test_mkv_zeroed_middle(fixtures, tools, tmp_path):
    need("ffmpeg", "ffprobe")
    src = fixtures / "repair" / "zeroed.mkv"
    need_decoders_for(src)
    before = shutil.copy(src, tmp_path / "copy.mkv")
    out_dir = tmp_path / "out"
    outcome = engine.repair(Path(before), out_dir, tools)
    assert outcome.output, outcome.message
    ctx = ctx_for(src, tools, tmp_path)
    errors, _t, _d = decode_check(ctx, outcome.output)
    assert errors == 0
    assert duration(ctx, outcome.output) >= 0.95 * duration(ctx, src)


def test_vob_remux_plays_start_to_end(fixtures, tools, tmp_path):
    need("ffmpeg", "ffprobe")
    src = fixtures / "repair" / "damaged.vob"
    need_decoders_for(src)
    ctx = ctx_for(src, tools, tmp_path)
    out = run_recipe(ctx, "Remux to MKV", tmp_path / "out.mkv")
    _errors, _t, decoded = decode_check(ctx, out)
    total = duration(ctx, src)
    # Plays to the end: decoding does not stop early, and both streams survive.
    assert decoded >= 0.95 * total
    kinds = {s["codec_type"] for s in ffprobe_json(ctx, out)["streams"]}
    assert {"video", "audio"} <= kinds


def _idx1_entries(path: Path) -> int:
    data = path.read_bytes()
    pos = data.find(b"idx1")
    if pos < 0:
        return 0
    return struct.unpack("<I", data[pos + 4:pos + 8])[0] // 16


def test_avi_index_rebuilt_and_seeks(fixtures, tools, tmp_path):
    need("ffmpeg", "ffprobe")
    src = fixtures / "repair" / "no_index.avi"
    ctx = ctx_for(src, tools, tmp_path)
    out = run_recipe(ctx, "Remux to AVI", tmp_path / "out.avi")
    assert _idx1_entries(out) > 0
    # Seek to 15 s and decode one frame: lands within a second of the target.
    res = subprocess.run(["ffprobe", "-v", "error", "-read_intervals", "15%+#1", "-select_streams", "v:0",
                          "-show_entries", "frame=pts_time,best_effort_timestamp_time", "-of", "csv=p=0",
                          str(out)], capture_output=True, text=True, check=True)
    first = float([x for x in res.stdout.split("\n")[0].split(",") if x and x != "N/A"][0])
    assert 14.0 <= first <= 16.0


@pytest.mark.parametrize("choice,codec,encoder", [("ffv1", "ffv1", "encoder ffv1"),
                                                  ("lossy", "hevc", "encoder libx265")])
def test_reencode_both_choices(fixtures, tools, tmp_path, choice, codec, encoder):
    need("ffmpeg", "ffprobe")
    need_codecs(tools, encoder)
    src = fixtures / "repair" / "needs_reencode.avi"
    asked = {}

    def approve(estimates):
        asked.update(estimates)
        return choice

    outcome = engine.repair(src, tmp_path, tools, lossy_codec="h265", approve=approve)
    assert asked, "re-encode ran without asking"
    assert set(asked) == {"ffv1", "lossy"} and all(v > 0 for v in asked.values())
    assert outcome.output and outcome.output.suffix == ".mkv"
    ctx = ctx_for(src, tools, tmp_path)
    info = ffprobe_json(ctx, outcome.output)
    assert any(s.get("codec_name") == codec for s in info["streams"])
    errors, _t, _d = decode_check(ctx, outcome.output)
    assert errors == 0


def test_jpeg_truncated(fixtures, tools, tmp_path):
    need("jpegtran", "exiftool")
    src = fixtures / "repair" / "truncated.jpg"
    outcome = engine.repair(src, tmp_path, tools)
    assert outcome.output and outcome.output.exists()
    if shutil.which("magick") or shutil.which("convert"):
        tool = shutil.which("magick") or shutil.which("convert")
        assert subprocess.run([tool, str(outcome.output), "null:"], capture_output=True).returncode == 0
    thumbs = [e for e in outcome.extras if "thumbnail" in e.name]
    assert thumbs and thumbs[0].read_bytes()[:2] == b"\xff\xd8"


def test_png_flipped_crc(fixtures, tools, tmp_path):
    need("pngfix", "pngcheck")
    src = fixtures / "repair" / "bad_crc.png"
    ctx = ctx_for(src, tools, tmp_path)
    out = run_recipe(ctx, "pngfix", tmp_path / "out.png")
    assert subprocess.run(["pngcheck", str(out)], capture_output=True).returncode == 0


def test_zip_without_central_directory(fixtures, tools, tmp_path):
    need("zip")
    src = fixtures / "repair" / "no_central_dir.zip"
    want = set((fixtures / "repair" / "no_central_dir.zip.names").read_text().split())
    ctx = ctx_for(src, tools, tmp_path)
    out = run_recipe(ctx, "zip -FF", tmp_path / "out.zip")
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        assert set(z.namelist()) == want


def test_pdf_broken_xref(fixtures, tools, tmp_path):
    need("qpdf")
    src = fixtures / "repair" / "broken_xref.pdf"
    ctx = ctx_for(src, tools, tmp_path)
    out = run_recipe(ctx, "qpdf", tmp_path / "out.pdf")
    res = subprocess.run(["qpdf", "--check", str(out)], capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def test_sqlite_corrupt_page(fixtures, tools, tmp_path):
    need("sqlite3")
    src = fixtures / "repair" / "corrupt.db"
    outcome = engine.repair(src, tmp_path, tools)
    assert outcome.output, outcome.message
    q = subprocess.run(["sqlite3", str(outcome.output), "PRAGMA integrity_check;"],
                       capture_output=True, text=True)
    assert q.stdout.strip() == "ok"
    # trips sits on pages the corruption did not touch: every row must be there.
    n = subprocess.run(["sqlite3", str(outcome.output), "SELECT count(*) FROM trips;"],
                       capture_output=True, text=True)
    assert n.stdout.strip() == "50"
    rows = subprocess.run(["sqlite3", str(outcome.output), "SELECT count(*) FROM photos;"],
                          capture_output=True, text=True)
    assert int(rows.stdout.strip()) > 0


def test_mov_that_is_mp4_is_mislabeled(fixtures, tools):
    need("fidentify", "file")
    src = fixtures / "repair" / "really_mp4.mov"
    with tempfile.TemporaryDirectory() as work:
        ctx = Ctx(src=src, work=Path(work), tools=tools)
        ident = identify.identify(ctx, src)
    assert ident.mislabeled
    assert any(f.kind == "mislabeled" for f in identify.findings(ident))


def test_every_repair_leaves_original_hash(fixtures, tools, tmp_path):
    for src in sorted((fixtures / "repair").iterdir()):
        if src.suffix in (".names",) or src.name == "reference.mp4":
            continue
        outcome = engine.repair(src, tmp_path / src.stem, tools)
        assert outcome.original_untouched, src.name
        assert outcome.report and outcome.report.exists()
