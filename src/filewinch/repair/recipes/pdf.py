"""PDF."""

from __future__ import annotations

from pathlib import Path

from filewinch.repair.common import Ctx, Finding, Recipe, Step, Verify

FAMILY = "PDF"
EXTS = {"pdf"}


def _check(ctx: Ctx, path: Path) -> tuple[bool, str]:
    res = ctx.run([ctx.tool("qpdf"), "--check", str(path)])
    text = res.stdout + "\n" + res.stderr
    # Exit 3 means warnings only. It passes when qpdf says the content is clean.
    ok = res.code == 0 or (res.code == 3 and "No syntax or stream encoding errors found" in text)
    return ok, text


def diagnose(ctx: Ctx) -> list[Finding]:
    if not ctx.have("qpdf"):
        return []
    ok, text = _check(ctx, ctx.src)
    if ok:
        return []
    lines = [l for l in text.splitlines() if "WARNING" in l or "ERROR" in l or "error" in l]
    return [Finding("pdf", f"qpdf --check reports {len(lines) or 'some'} problem(s).",
                    (lines or [""])[0].strip())]


def _qpdf(c: Ctx, out: Path) -> list[Step]:
    return [Step([c.tool("qpdf"), str(c.src), str(out)], ok_codes=(0, 3))]


def _mutool(c: Ctx, out: Path) -> list[Step]:
    return [Step([c.tool("mutool"), "clean", "-s", str(c.src), str(out)])]


def recipes(ctx: Ctx) -> list[Recipe]:
    return [Recipe("qpdf rebuild", "pdf", _qpdf, needs=("qpdf",)),
            Recipe("mutool clean", "pdf", _mutool, needs=("mutool",))]


def verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    if not ctx.have("qpdf"):
        return Verify(True, detail="qpdf missing, not checked")
    ok, _text = _check(ctx, out)
    return Verify(ok, 0 if ok else 1, detail="qpdf --check " + ("passes" if ok else "fails"))
