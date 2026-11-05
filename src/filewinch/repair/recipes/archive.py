"""ZIP and everything built on it: DOCX, XLSX, PPTX, ODT, ODS, ODP, EPUB."""

from __future__ import annotations

import shutil
from pathlib import Path

from filewinch.repair.common import Ctx, Finding, Recipe, Step, Verify

FAMILY = "ZIP / Office"
OFFICE = {"docx", "xlsx", "pptx", "odt", "ods", "odp"}
EXTS = {"zip", "epub", "jar"} | OFFICE


def _test(ctx: Ctx, path: Path):
    if ctx.have("7z"):
        return ctx.run([ctx.tool("7z"), "t", "-bd", str(path)])
    if ctx.have("zip"):
        return ctx.run([ctx.tool("zip"), "-T", str(path)])
    return ctx.run([ctx.tool("bsdtar"), "-tf", str(path)])


def diagnose(ctx: Ctx) -> list[Finding]:
    res = _test(ctx, ctx.src)
    if res.ok:
        return []
    lines = [l for l in (res.stdout + "\n" + res.stderr).splitlines()
             if any(w in l for w in ("ERROR", "Error", "error", "CRC", "Unexpected", "Headers"))]
    return [Finding("archive", "The archive test fails.", (lines or ["exit code " + str(res.code)])[0].strip())]


def _zipff(c: Ctx, out: Path) -> list[Step]:
    # zip -FF can ask whether this is a single-disk archive. The answer is yes.
    answer = c.work / "answer.txt"
    answer.write_text("y\n")
    return [Step([c.tool("zip"), "-FF", str(c.src), "--out", str(out)], stdin_file=str(answer),
                 ok_codes=(0, 2, 3, 12))]


def _office(c: Ctx, out: Path) -> bool:
    """zip -FF, then let LibreOffice load and re-save the result."""
    stage = c.work / f"stage.{c.real_ext}"
    res = c.run([c.tool("zip"), "-FF", str(c.src), "--out", str(stage)], stdin_file=_answer(c))
    if not stage.exists():
        return False
    conv = c.work / "converted"
    conv.mkdir(exist_ok=True)
    c.run([c.tool("soffice"), "--headless", "--norestore", f"-env:UserInstallation=file://{c.work}/lo-profile",
           "--convert-to", c.real_ext, "--outdir", str(conv), str(stage)], timeout=600)
    made = conv / stage.name
    if not made.exists():
        return False
    shutil.move(str(made), str(out))
    return res.code in (0, 2, 3, 12)


def _answer(c: Ctx) -> str:
    answer = c.work / "answer.txt"
    answer.write_text("y\n")
    return str(answer)


def recipes(ctx: Ctx) -> list[Recipe]:
    chain = [Recipe("zip -FF container repair", ctx.real_ext, _zipff, needs=("zip",))]
    if ctx.real_ext in OFFICE:
        chain.append(Recipe("zip -FF, then LibreOffice re-save", ctx.real_ext, lambda c, o: [],
                            needs=("zip", "soffice"), custom=_office))
    return chain


def verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    res = _test(ctx, out)
    return Verify(res.ok, 0 if res.ok else 1, detail="archive test " + ("passes" if res.ok else "fails"))
