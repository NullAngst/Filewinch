"""Run one file through identify, diagnose, the recipe chain and verify.

Recipes run top to bottom and stop at the first one that verifies. Lossless
recipes always come first. A re-encode runs only when the approve callback
says so. If nothing fully verifies, the best output by error count and
recovered duration is kept, and the report says so.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from filewinch.core.jobs import CancelToken
from filewinch.core.safety import sha256
from filewinch.repair import diagnose as diag
from filewinch.repair import identify as ident_mod
from filewinch.repair import recipes as recipe_pkg
from filewinch.repair.common import Ctx, Finding, Recipe, Verify, estimate_reencode_sizes, hms, size_text

# approve(estimates) -> "ffv1", "lossy" or None. estimates: {"ffv1": bytes, "lossy": bytes}
Approve = Callable[[dict], str | None]


@dataclass
class Attempt:
    recipe: str
    ran: bool
    verify: Verify | None = None
    skipped: str = ""
    output: Path | None = None
    commands: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    source: Path
    real_ext: str = ""
    family: str = ""
    findings: list[Finding] = field(default_factory=list)
    attempts: list[Attempt] = field(default_factory=list)
    output: Path | None = None
    fully_verified: bool = False
    winner: str = ""
    report: Path | None = None
    extras: list[Path] = field(default_factory=list)
    hash_before: str = ""
    hash_after: str = ""
    source_errors: int | None = None
    message: str = ""
    mediainfo: str = ""

    @property
    def original_untouched(self) -> bool:
        return bool(self.hash_before) and self.hash_before == self.hash_after


def _run_recipe(ctx: Ctx, recipe: Recipe, out: Path, attempt: Attempt) -> bool:
    from filewinch.core.jobs import show_command
    if recipe.custom:
        attempt.commands.append(f"({recipe.name}: several commands, see the log)")
        return recipe.custom(ctx, out)
    for step in recipe.build(ctx, out):
        attempt.commands.append(show_command(step.argv, step.stdout_file, step.stdin_file))
        res = ctx.run(step.argv, stdout_file=step.stdout_file, stdin_file=step.stdin_file,
                      cwd=step.cwd or None, keep_stdout=False)
        if res.cancelled:
            return False
        if step.ok_codes and res.code not in step.ok_codes:
            return False
    return out.exists()


def _missing_tools(ctx: Ctx, recipe: Recipe) -> list[str]:
    return [t for t in recipe.needs if not ctx.have(t)]


def _score(a: Attempt) -> tuple:
    """Best attempt when none verifies: keeps every stream and most of the data,
    then fewest errors, then longest. A clean file with the video gone, or with
    one second left of twenty, is not a repair."""
    v = a.verify
    return (v.lost, v.errors if v.errors >= 0 else 10**9, -v.duration)


def repair(src: Path, out_dir: Path, tools, *, lossy_codec: str = "h265", reference: Path | None = None,
           approve: Approve | None = None, log=None, cancel: CancelToken | None = None,
           on_event: Callable[[str, object], None] | None = None) -> Outcome:
    """on_event(kind, payload) for the UI: "identified" (Ident), "findings"
    (list), "attempt" (Attempt, sent before and after it runs), "done" (Outcome)."""
    emit = on_event or (lambda _k, _p: None)
    src = Path(src).resolve()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outcome = Outcome(source=src)
    outcome.hash_before = sha256(str(src))
    work = Path(tempfile.mkdtemp(prefix=".filewinch-work-", dir=out_dir))
    ctx = Ctx(src=src, work=work, tools=tools, log=log, cancel=cancel, lossy_codec=lossy_codec,
              reference=Path(reference).resolve() if reference else None)
    try:
        ident = ident_mod.identify(ctx, src)
        ctx.real_ext = ident.real
        ctx.info = ident.info
        ctx.findings = ident_mod.findings(ident)
        outcome.real_ext = ident.real
        emit("identified", ident)
        module = recipe_pkg.for_ext(ident.real)
        if module is None:
            outcome.findings = ctx.findings + diag.zeros(ctx) + [
                Finding("unsupported", f"No repair chain for {ident.real.upper() or 'this type'} "
                                       f"({ident.description or ident.mime}).")]
            outcome.message = "No repair chain for this file type."
            emit("findings", outcome.findings)
            return outcome
        outcome.family = module.FAMILY
        if log:
            log(f"{src.name}: {module.FAMILY} ({ident.description})")
        outcome.findings = diag.run_all(ctx, module)
        if ctx.have("mediainfo") and ident.info:
            mi = ctx.run([ctx.tool("mediainfo"), "--Inform=General;%Format% %Duration/String3% %OverallBitRate/String%",
                          str(src)])
            if mi.stdout.strip():
                outcome.mediainfo = mi.stdout.strip()
        emit("findings", outcome.findings)
        for f in outcome.findings:
            if f.kind == "decode-errors":
                try:
                    outcome.source_errors = int(f.text.split()[0])
                except ValueError:
                    pass

        chain = module.recipes(ctx)
        plain = [r for r in chain if not r.reencode]
        reenc = [r for r in chain if r.reencode]
        winner: Attempt | None = None
        for i, recipe in enumerate(plain, 1):
            if cancel and cancel.cancelled:
                break
            attempt = Attempt(recipe=recipe.name, ran=False)
            outcome.attempts.append(attempt)
            missing = _missing_tools(ctx, recipe)
            if missing:
                attempt.skipped = "needs " + ", ".join(missing)
                emit("attempt", attempt)
                continue
            if recipe.needs_reference and not ctx.reference:
                attempt.skipped = "needs a reference file. " + recipe.note
                emit("attempt", attempt)
                continue
            out = work / f"try{i}.{recipe.out_ext}"
            attempt.ran = True
            emit("attempt", attempt)
            produced = _run_recipe(ctx, recipe, out, attempt)
            attempt.output = out if produced and out.exists() else None
            attempt.verify = module.verify(ctx, out) if attempt.output else Verify(False, -1, detail="no output")
            if log:
                log(f"{'PASS' if attempt.verify.ok else 'FAIL'} {recipe.name}: {attempt.verify.detail}")
            emit("attempt", attempt)
            if attempt.verify.ok:
                winner = attempt
                break

        if winner is None and reenc and approve and not (cancel and cancel.cancelled):
            choice = approve(estimate_reencode_sizes(ctx))
            pick = {"ffv1": reenc[0], "lossy": reenc[-1]}.get(choice or "")
            if pick:
                attempt = Attempt(recipe=pick.name, ran=True)
                outcome.attempts.append(attempt)
                missing = _missing_tools(ctx, pick)
                if missing:
                    attempt.ran = False
                    attempt.skipped = "needs " + ", ".join(missing)
                else:
                    emit("attempt", attempt)
                    out = work / f"reencode.{pick.out_ext}"
                    produced = _run_recipe(ctx, pick, out, attempt)
                    attempt.output = out if produced else None
                    attempt.verify = module.verify(ctx, out) if produced else Verify(False, -1, detail="no output")
                emit("attempt", attempt)
                if attempt.verify and attempt.verify.ok:
                    winner = attempt
        elif winner is None and reenc and approve is None:
            outcome.attempts.append(Attempt(recipe="Re-encode (last resort)", ran=False,
                                            skipped="waiting for your OK"))

        if winner is None:
            candidates = [a for a in outcome.attempts if a.output and a.verify]
            if candidates:
                winner = min(candidates, key=_score)
        else:
            outcome.fully_verified = True

        if winner and winner.output:
            ext = winner.output.suffix
            final = out_dir / f"{src.stem}.repaired{ext}"
            n = 1
            while final.exists():
                final = out_dir / f"{src.stem}.repaired.{n}{ext}"
                n += 1
            shutil.move(str(winner.output), str(final))
            winner.output = final
            outcome.output = final
            outcome.winner = winner.recipe
            if hasattr(module, "extras"):
                outcome.extras = module.extras(ctx, out_dir)
            outcome.message = ("Repaired and verified." if outcome.fully_verified else
                               "Nothing fully verified. Kept the best attempt by error count and duration.")
        else:
            outcome.message = "No recipe produced a usable file."
    finally:
        shutil.rmtree(work, ignore_errors=True)
        outcome.hash_after = sha256(str(src))
    outcome.report = write_report(outcome, out_dir)
    emit("done", outcome)
    return outcome


def report_text(o: Outcome) -> str:
    lines = [f"FileWinch repair report, {_dt.datetime.now():%Y-%m-%d %H:%M}",
             "", f"Source: {o.source}",
             f"Type:   {o.real_ext.upper()} ({o.family or 'no chain'})"]
    if o.mediainfo:
        lines.append(f"Media:  {o.mediainfo}")
    lines += ["", "What was wrong:"]
    if o.findings:
        for f in o.findings:
            lines.append(f"  - {f.text}" + (f" [{f.evidence}]" if f.evidence else ""))
    else:
        lines.append("  - No problems found by the checks.")
    lines += ["", "Recipes tried:"]
    for a in o.attempts:
        if not a.ran:
            lines.append(f"  [skip] {a.recipe}: {a.skipped}")
            continue
        v = a.verify
        mark = "[ok]  " if v and v.ok else "[fail]"
        lines.append(f"  {mark} {a.recipe}: {v.detail if v else 'no result'}")
        for c in a.commands:
            lines.append(f"         $ {c}")
    lines += ["", f"Result: {o.message}"]
    if o.output:
        lines.append(f"Output: {o.output} ({size_text(o.output.stat().st_size)})")
        lines.append(f"Recipe that worked: {o.winner}")
    if o.source_errors is not None:
        best = next((a.verify for a in o.attempts if a.output and a.output == o.output and a.verify), None)
        after = best.errors if best else "n/a"
        lines.append(f"Decode errors before: {o.source_errors}, after: {after}")
    for e in o.extras:
        lines.append(f"Also saved: {e}")
    lines += ["", f"Original SHA-256 before: {o.hash_before}",
              f"Original SHA-256 after:  {o.hash_after}",
              "Original untouched: " + ("yes" if o.original_untouched else "NO, INVESTIGATE")]
    return "\n".join(lines) + "\n"


def write_report(o: Outcome, out_dir: Path) -> Path:
    base = o.output.name if o.output else f"{o.source.name}.failed"
    path = out_dir / f"{base}.report.txt"
    path.write_text(report_text(o))
    return path


__all__ = ["repair", "Outcome", "Attempt", "report_text", "hms"]
