"""Per-format recipe chains.

Each module in this package defines:

    FAMILY   short name shown in the UI
    EXTS     set of real extensions it handles
    diagnose(ctx) -> list[Finding]
    recipes(ctx)  -> list[Recipe], lossless first, re-encodes last
    verify(ctx, out) -> Verify
    extras(ctx, out_dir) -> list[Path]     optional, files saved beside the result

Adding a format is one new file here. Nothing else needs to change.
"""

from __future__ import annotations

import importlib
import pkgutil


def modules() -> list:
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        if hasattr(mod, "EXTS") and hasattr(mod, "recipes"):
            found.append(mod)
    return found


def for_ext(ext: str):
    for mod in modules():
        if ext in mod.EXTS:
            return mod
    return None


def supported_exts() -> set[str]:
    out: set[str] = set()
    for mod in modules():
        out |= set(mod.EXTS)
    return out
