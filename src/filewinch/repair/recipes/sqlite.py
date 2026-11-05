"""SQLite databases."""

from __future__ import annotations

from pathlib import Path

from filewinch.repair.common import Ctx, Finding, Recipe, Step, Verify

FAMILY = "SQLite"
EXTS = {"sqlite"}


def _integrity(ctx: Ctx, path: Path, readonly: bool = True) -> tuple[bool, str]:
    # -readonly so nothing (journal, -wal, -shm) is ever written beside the original.
    argv = [ctx.tool("sqlite3")] + (["-readonly"] if readonly else []) + [str(path), "PRAGMA integrity_check;"]
    res = ctx.run(argv)
    text = (res.stdout + "\n" + res.stderr).strip()
    return res.ok and res.stdout.strip() == "ok", text


def diagnose(ctx: Ctx) -> list[Finding]:
    if not ctx.have("sqlite3"):
        return []
    ok, text = _integrity(ctx, ctx.src)
    if ok:
        return []
    lines = text.splitlines()
    return [Finding("integrity", f"PRAGMA integrity_check reports {len(lines)} problem(s).", lines[0][:200] if lines else "")]


def _recover(c: Ctx, out: Path) -> list[Step]:
    dump = c.work / "recover.sql"
    if out.exists():
        out.unlink()
    return [Step([c.tool("sqlite3"), "-readonly", str(c.src), ".recover"], stdout_file=str(dump)),
            Step([c.tool("sqlite3"), str(out)], stdin_file=str(dump))]


def _dump(c: Ctx, out: Path) -> bool:
    """For sqlite3 builds without the dbpage table that .recover needs.

    .dump reads every row it can reach and ends with ROLLBACK when it hit a bad
    page. Committing instead keeps the rows it did read.
    """
    dump = c.work / "dump.sql"
    c.run([c.tool("sqlite3"), "-readonly", str(c.src), ".dump"], stdout_file=str(dump))
    if not dump.exists() or dump.stat().st_size == 0:
        return False
    text = dump.read_text(errors="replace")
    if text.rstrip().endswith("ROLLBACK; -- due to errors"):
        text = text.rstrip()[: -len("ROLLBACK; -- due to errors")] + "COMMIT;\n"
        dump.write_text(text)
    if out.exists():
        out.unlink()
    res = c.run([c.tool("sqlite3"), str(out)], stdin_file=str(dump))
    return res.ok and out.exists()


def recipes(ctx: Ctx) -> list[Recipe]:
    return [Recipe("sqlite3 .recover into a new database", "sqlite", _recover, needs=("sqlite3",)),
            Recipe("sqlite3 .dump, keeping every row it can read", "sqlite", lambda c, o: [],
                   needs=("sqlite3",), custom=_dump)]


def verify(ctx: Ctx, out: Path) -> Verify:
    if not out.exists() or out.stat().st_size == 0:
        return Verify(False, detail="no output")
    ok, text = _integrity(ctx, out)
    return Verify(ok, 0 if ok else 1, detail=text[:120])
