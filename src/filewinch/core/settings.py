"""Settings in ~/.config/filewinch/settings.ini."""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass, fields
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "filewinch"
CONFIG_FILE = CONFIG_DIR / "settings.ini"



@dataclass
class Settings:
    output_dir: str = str(Path.home() / "FileWinch")
    rename_by_date: bool = False
    auto_repair_videos: bool = False
    lossy_codec: str = "h265"       # h265 or h264
    theme: str = "dark"
    theme_follows_system: bool = True

    @classmethod
    def load(cls, path: Path = CONFIG_FILE) -> "Settings":
        s = cls()
        cp = configparser.ConfigParser()
        cp.read(path)
        if cp.has_section("filewinch"):
            sec = cp["filewinch"]
            for f in fields(cls):
                if f.name not in sec:
                    continue
                if f.type in ("bool", bool):
                    setattr(s, f.name, sec.getboolean(f.name))
                else:
                    setattr(s, f.name, sec.get(f.name))
        if not s.theme:
            s.theme = "dark"  # an unknown name falls back to Dark in ui/theme.py
        if s.lossy_codec not in ("h264", "h265"):
            s.lossy_codec = "h265"
        return s

    def save(self, path: Path = CONFIG_FILE) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        cp = configparser.ConfigParser()
        cp["filewinch"] = {f.name: str(getattr(self, f.name)).lower() if isinstance(getattr(self, f.name), bool)
                           else str(getattr(self, f.name)) for f in fields(self)}
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            cp.write(fh)
        tmp.replace(path)
