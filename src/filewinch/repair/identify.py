"""What is this file really? fidentify and file for the type, ffprobe for media."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from filewinch.core.jobs import run_command
from filewinch.repair.common import Ctx, Finding, ffprobe_json

# fidentify reports PhotoRec extensions. Map aliases to one name per format.
ALIASES = {
    "jpeg": "jpg", "jpe": "jpg", "tif": "tiff", "mpeg": "mpg", "m2ts": "m2ts", "mts": "m2ts",
    "qt": "mov", "aif": "aiff", "aifc": "aiff", "sqlite3": "sqlite", "db": "sqlite",
    "mka": "mkv", "mk3d": "mkv",
}

# file(1) MIME types for when fidentify says unknown
MIME_TO_EXT = {
    "video/mp4": "mp4", "video/quicktime": "mov", "video/x-matroska": "mkv", "video/webm": "webm",
    "video/x-msvideo": "avi", "video/mpeg": "mpg", "video/mp2t": "ts", "audio/mpeg": "mp3",
    "audio/flac": "flac", "audio/x-flac": "flac", "audio/x-wav": "wav", "audio/wav": "wav",
    "audio/x-aiff": "aiff", "image/jpeg": "jpg", "image/png": "png", "application/zip": "zip",
    "application/pdf": "pdf", "application/vnd.sqlite3": "sqlite", "application/x-sqlite3": "sqlite",
    "application/epub+zip": "epub",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
    "image/x-canon-cr2": "cr2", "image/x-nikon-nef": "nef", "image/x-sony-arw": "arw",
}

# Extensions that are the same family, so a mismatch between them is not news.
EQUIVALENT = [
    {"mp4", "m4v", "m4a", "3gp"},
    {"mkv", "webm"},
    {"mpg", "vob", "vro", "m2v", "ps"},
    {"ts", "m2ts"},
    {"zip", "docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "jar"},
    {"tiff", "nef", "dng", "arw", "orf", "rw2", "pef", "srw", "cr2"},
]


@dataclass
class Ident:
    ext: str            # extension of the file name
    real: str           # what fidentify / file say it is
    mime: str
    description: str
    info: dict

    @property
    def mislabeled(self) -> bool:
        if not self.real or not self.ext or self.real == self.ext:
            return False
        return not any(self.real in g and self.ext in g for g in EQUIVALENT)


def norm(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    return ALIASES.get(ext, ext)


def _fidentify(ctx: Ctx, path: Path) -> str:
    tool = ctx.tool("fidentify")
    if not tool:
        return ""
    # fidentify writes fidentify.log into its working directory; keep it in the work folder.
    res = run_command([tool, str(path)], cancel=ctx.cancel, timeout=120, cwd=str(ctx.work))
    m = re.search(r":\s*(\S+)\s*$", res.stdout.strip().splitlines()[-1] if res.stdout.strip() else "")
    if not m or m.group(1) == "unknown":
        return ""
    return norm(m.group(1))


def _file(ctx: Ctx, path: Path) -> tuple[str, str]:
    tool = ctx.tool("file")
    if not tool:
        return "", ""
    mime = run_command([tool, "-b", "--mime-type", str(path)], timeout=60).stdout.strip()
    desc = run_command([tool, "-b", str(path)], timeout=60).stdout.strip()
    return mime, desc


def _office_flavour(ctx: Ctx, path: Path, current: str) -> str:
    """fidentify often says zip for Office files. Look inside with 7z or bsdtar."""
    lister = ctx.tool("7z")
    argv = [lister, "l", "-ba", str(path)] if lister else ([ctx.tool("bsdtar"), "-tf", str(path)] if ctx.have("bsdtar") else [])
    if not argv:
        return current
    names = run_command(argv, timeout=60).stdout
    if "word/document.xml" in names:
        return "docx"
    if "xl/workbook.xml" in names:
        return "xlsx"
    if "ppt/presentation.xml" in names:
        return "pptx"
    if "META-INF/container.xml" in names:
        return "epub"
    return current


def identify(ctx: Ctx, path: Path) -> Ident:
    ext = norm(path.suffix)
    real = _fidentify(ctx, path)
    mime, desc = _file(ctx, path)
    if not real:
        real = MIME_TO_EXT.get(mime, "")
    if real == "mpg" and ext in ("vob", "vro"):
        real = ext
    if real == "zip":
        real = _office_flavour(ctx, path, real)
    if not real:
        real = ext
    info = ffprobe_json(ctx, path) if mime.startswith(("video", "audio")) or real in (
        "mp4", "mov", "mkv", "webm", "avi", "mpg", "vob", "vro", "ts", "m2ts", "mp3", "flac", "wav", "aiff") else {}
    return Ident(ext=ext, real=real, mime=mime, description=desc, info=info)


def findings(ident: Ident) -> list[Finding]:
    out = []
    if ident.mislabeled:
        out.append(Finding("mislabeled",
                           f"Mislabeled: the name says .{ident.ext} but it is really {ident.real.upper()}.",
                           ident.description))
    return out
