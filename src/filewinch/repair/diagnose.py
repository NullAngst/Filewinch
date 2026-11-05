"""Checks that apply to every format: zero runs, size versus declared size."""

from __future__ import annotations

from filewinch.repair.common import ZERO_BLOCK, Ctx, Finding, size_text, zero_runs


def zeros(ctx: Ctx) -> list[Finding]:
    runs = zero_runs(ctx.src)
    if not runs:
        return []
    total = sum(n for _o, n in runs)
    biggest = max(runs, key=lambda r: r[1])
    return [Finding(
        "zeros",
        f"{len(runs)} region(s) of {ZERO_BLOCK // 1024} KiB or more are all zeros, {size_text(total)} in total. "
        "That usually means TRIM or a carve that grabbed empty space.",
        f"largest at byte {biggest[0]}, {size_text(biggest[1])}",
    )]


def decode_summary(errors: int, rngs: list[tuple[float, float]]) -> Finding | None:
    from filewinch.repair.common import hms
    if errors <= 0:
        return None
    if rngs:
        first, last = rngs[0][0], rngs[-1][1]
        where = f" between {hms(first)} and {hms(last)}"
        if len(rngs) > 1:
            where += f" in {len(rngs)} separate stretches"
    else:
        where = ""
    return Finding("decode-errors", f"{errors} decode errors{where}.",
                   "; ".join(f"{hms(a)}-{hms(b)}" for a, b in rngs[:10]))


def run_all(ctx: Ctx, module) -> list[Finding]:
    found = list(ctx.findings)
    found.extend(module.diagnose(ctx))
    found.extend(zeros(ctx))
    return found
