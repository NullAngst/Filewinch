"""JPEG, PNG and camera RAW."""

from __future__ import annotations

from pathlib import Path

from filewinch.repair.common import Ctx, Finding, Recipe, Step, Verify

FAMILY = "Image"
RAW = {"cr2", "cr3", "nef", "nrw", "arw", "dng", "orf", "rw2", "raf", "pef", "srw", "crw", "x3f"}
EXTS = {"jpg", "png"} | RAW


def _jpeg_ok(res) -> bool:
    text = res.stdout.upper()
    return res.code == 0 and "ERROR" not in text and "WARNING" not in text


def diagnose(ctx: Ctx) -> list[Finding]:
    out = []
    ext = ctx.real_ext
    if ext == "jpg" and ctx.have("jpeginfo"):
        res = ctx.run([ctx.tool("jpeginfo"), "-c", str(ctx.src)])
        line = (res.stdout.strip().splitlines() or [""])[-1]
        if not _jpeg_ok(res):
            out.append(Finding("jpeg", "jpeginfo finds damage in the JPEG data.", line))
    elif ext == "png" and ctx.have("pngcheck"):
        res = ctx.run([ctx.tool("pngcheck"), "-v", str(ctx.src)])
        if res.code != 0:
            bad = [l for l in res.stdout.splitlines() if "ERROR" in l or "error" in l or "CRC" in l]
            out.append(Finding("png", "pngcheck reports errors.", (bad or [res.stdout.strip()[-200:]])[0]))
    elif ext in RAW and ctx.have("exiftool"):
        res = ctx.run([ctx.tool("exiftool"), "-s", "-PreviewImage", "-JpgFromRaw", "-ThumbnailImage", str(ctx.src)])
        if res.stdout.strip():
            out.append(Finding("previews", "Embedded previews found: " +
                               ", ".join(l.split(":")[0].strip() for l in res.stdout.splitlines()) + "."))
        else:
            out.append(Finding("no-previews", "No embedded preview survives in this RAW file."))
    return out


def _jpegtran(c: Ctx, out: Path) -> list[Step]:
    # Exit 2 means warnings (premature end of data and so on), which is the point.
    return [Step([c.tool("jpegtran"), "-copy", "all", "-outfile", str(out), str(c.src)], ok_codes=(0, 2))]


def _exif_preview(tag: str):
    def build(c: Ctx, out: Path) -> list[Step]:
        return [Step([c.tool("exiftool"), "-b", f"-{tag}", str(c.src)], stdout_file=str(out))]
    return build


def _pngfix(c: Ctx, out: Path) -> list[Step]:
    # pngfix uses its exit code as a bit mask of what it found; the verify decides.
    return [Step([c.tool("pngfix"), f"--out={out}", str(c.src)], ok_codes=tuple(range(0, 256)))]


def _magick(c: Ctx, out: Path) -> list[Step]:
    return [Step([c.tool("magick"), str(c.src), str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    ext = ctx.real_ext
    if ext == "jpg":
        return [Recipe("jpegtran rewrite", "jpg", _jpegtran, needs=("jpegtran",)),
                Recipe("Embedded preview", "jpg", _exif_preview("PreviewImage"), needs=("exiftool",)),
                Recipe("Embedded thumbnail", "jpg", _exif_preview("ThumbnailImage"), needs=("exiftool",)),
                Recipe("ImageMagick decode", "jpg", _magick, needs=("magick",))]
    if ext == "png":
        return [Recipe("pngfix", "png", _pngfix, needs=("pngfix",)),
                Recipe("ImageMagick decode to a new PNG", "png", _magick, needs=("magick",))]
    return [Recipe("Full-size embedded JPEG", "jpg", _exif_preview("JpgFromRaw"), needs=("exiftool",)),
            Recipe("Embedded preview", "jpg", _exif_preview("PreviewImage"), needs=("exiftool",))]


def verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    if out.suffix == ".png" and ctx.have("pngcheck"):
        res = ctx.run([ctx.tool("pngcheck"), str(out)])
        return Verify(res.code == 0, 0 if res.code == 0 else 1, detail=res.stdout.strip()[-120:])
    if out.suffix == ".jpg":
        if ctx.have("jpeginfo"):
            res = ctx.run([ctx.tool("jpeginfo"), "-c", str(out)])
            ok = _jpeg_ok(res)
            return Verify(ok, 0 if ok else 1, detail=res.stdout.strip()[-120:])
        with open(out, "rb") as fh:
            ok = fh.read(2) == b"\xff\xd8"
        return Verify(ok, 0 if ok else 1)
    return Verify(True)


def extras(ctx: Ctx, out_dir: Path) -> list[Path]:
    """Save the embedded thumbnail separately, whatever won the chain."""
    if ctx.real_ext != "jpg" or not ctx.have("exiftool"):
        return []
    saved = []
    for tag, suffix in (("ThumbnailImage", "thumbnail"), ("PreviewImage", "preview")):
        dest = out_dir / f"{ctx.src.stem}.{suffix}.jpg"
        res = ctx.run([ctx.tool("exiftool"), "-b", f"-{tag}", str(ctx.src)], stdout_file=str(dest))
        if res.ok and dest.exists() and dest.stat().st_size > 0:
            saved.append(dest)
        elif dest.exists():
            dest.unlink()
    return saved
