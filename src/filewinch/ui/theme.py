"""Themes: base.qss plus one file per theme with a palette block.

A theme file starts with a comment like:

    /* palette
    name: Verdigris
    dark: true
    bg: #16211f
    ...
    */

Every key becomes @key in base.qss. Anything after the palette block is extra
QSS for that theme. Drop a new .qss into themes/ and it shows up in Settings.
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtGui import QColor, QGuiApplication, QPalette
from PyQt6.QtCore import Qt

THEME_DIR = Path(__file__).resolve().parent.parent / "themes"
PALETTE_RE = re.compile(r"/\*\s*palette\s*\n(.*?)\*/", re.S)


@dataclass
class Theme:
    key: str
    name: str
    order: int
    dark: bool
    colors: dict
    extra: str


def _parse(path: Path) -> Theme | None:
    text = path.read_text(encoding="utf-8")
    m = PALETTE_RE.search(text)
    if not m:
        return None
    values = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            values[k.strip()] = v.strip()
    meta = {k: values.pop(k, "") for k in ("name", "order", "dark")}
    return Theme(key=path.stem, name=meta["name"] or path.stem, order=int(meta["order"] or 99),
                 dark=meta["dark"].lower() == "true", colors=values, extra=text[m.end():])


def available() -> list[Theme]:
    themes = [t for t in (_parse(p) for p in sorted(THEME_DIR.glob("*.qss")) if p.stem != "base") if t]
    return sorted(themes, key=lambda t: (t.order, t.name))


def get(key: str) -> Theme:
    themes = {t.key: t for t in available()}
    return themes.get(key) or themes.get("dark") or next(iter(themes.values()))


def stylesheet(theme: Theme) -> str:
    base = (THEME_DIR / "base.qss").read_text(encoding="utf-8")
    # Longest names first so @warn_bg is not eaten by @warn.
    for k in sorted(theme.colors, key=len, reverse=True):
        base = base.replace("@" + k, theme.colors[k])
    return base + "\n" + theme.extra


def palette(theme: Theme) -> QPalette:
    c = {k: QColor(v) for k, v in theme.colors.items()}
    p = QPalette()
    R = QPalette.ColorRole
    p.setColor(R.Window, c["bg"])
    p.setColor(R.WindowText, c["fg"])
    p.setColor(R.Base, c["input"])
    p.setColor(R.AlternateBase, c["panel"])
    p.setColor(R.Text, c["fg"])
    p.setColor(R.Button, c["button"])
    p.setColor(R.ButtonText, c["fg"])
    p.setColor(R.Highlight, c["accent"])
    p.setColor(R.HighlightedText, c["accent_fg"])
    p.setColor(R.ToolTipBase, c["panel"])
    p.setColor(R.ToolTipText, c["fg"])
    p.setColor(R.PlaceholderText, c["muted"])
    p.setColor(R.Link, c["accent"])
    for role in (R.WindowText, R.Text, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, c["muted"])
    return p


def system_prefers_dark() -> bool | None:
    """True or False when the desktop says, None when it does not."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Dark:
            return True
        if scheme == Qt.ColorScheme.Light:
            return False
    except AttributeError:
        pass
    kdeglobals = Path.home() / ".config" / "kdeglobals"
    if kdeglobals.exists():
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(kdeglobals)
        except configparser.Error:
            return None
        rgb = cp.get("Colors:Window", "BackgroundNormal", fallback="")
        parts = [int(x) for x in rgb.split(",")[:3] if x.strip().isdigit()]
        if len(parts) == 3:
            return (0.299 * parts[0] + 0.587 * parts[1] + 0.114 * parts[2]) < 128
        scheme_name = cp.get("General", "ColorScheme", fallback="")
        if scheme_name:
            return "dark" in scheme_name.lower()
    return None


def resolve(key: str, follow_system: bool) -> Theme:
    """Dark is the default. Following the system flips between Light and Dark only."""
    if follow_system and key in ("light", "dark"):
        pref = system_prefers_dark()
        if pref is not None:
            return get("dark" if pref else "light")
    return get(key)


def apply(app, key: str, follow_system: bool = False) -> Theme:
    theme = resolve(key, follow_system)
    app.setStyle("Fusion")
    app.setPalette(palette(theme))
    app.setStyleSheet(stylesheet(theme))
    return theme


def color(theme: Theme, name: str) -> QColor:
    return QColor(theme.colors.get(name, "#888888"))
