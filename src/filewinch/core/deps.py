"""Find every tool in deps.toml, read its version, and build install commands.

Nothing here installs anything. It reports, and the Dependencies tab shows the
report with a command the user can copy.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from filewinch.core.jobs import host_env

DEPS_FILE = Path(__file__).resolve().parent.parent / "deps.toml"

# Order matters: the first manager found wins.
PACKAGE_MANAGERS = ["apt", "dnf", "zypper", "pacman", "apk", "emerge", "xbps-install", "eopkg"]

INSTALL_TEMPLATES = {
    "apt": "sudo apt install {pkgs}",
    "dnf": "sudo dnf install {pkgs}",
    "zypper": "sudo zypper install {pkgs}",
    "pacman": "sudo pacman -S --needed {pkgs}",
}

GENERIC_VERSION_RE = r"(\d+\.\d+(?:\.\d+)*)"

FOUND = "found"
MISSING = "missing"
OUTDATED = "outdated"
LIMITED = "limited"


@dataclass
class Tool:
    key: str
    required: bool
    feature: str
    binaries: list[str]
    packages: dict[str, str]
    group: str = ""
    version_cmd: list[str] = field(default_factory=list)
    version_re: str = GENERIC_VERSION_RE
    min_version: str = ""
    cve: str = ""
    bundled: bool = False
    limited_note: str = ""
    upstream_note: str = ""
    extra_dirs: list[str] = field(default_factory=list)
    # Filled by scan()
    path: str = ""
    version: str = ""
    status: str = MISSING
    detail: str = ""


def load(deps_file: Path = DEPS_FILE) -> tuple[dict[str, Tool], dict]:
    with open(deps_file, "rb") as fh:
        data = tomllib.load(fh)
    tools = {}
    for key, raw in data.get("tools", {}).items():
        tools[key] = Tool(
            key=key,
            required=bool(raw.get("required", False)),
            feature=raw.get("feature", ""),
            binaries=list(raw.get("binaries", [key])),
            packages=dict(raw.get("packages", {})),
            group=raw.get("group", ""),
            version_cmd=list(raw.get("version_cmd", [])),
            version_re=raw.get("version_re", GENERIC_VERSION_RE),
            min_version=raw.get("min_version", ""),
            cve=raw.get("cve", ""),
            bundled=bool(raw.get("bundled", False)),
            limited_note=raw.get("limited_note", ""),
            upstream_note=raw.get("upstream_note", ""),
            extra_dirs=list(raw.get("extra_dirs", [])),
        )
    return tools, data.get("ffmpeg_codecs", {})


def detect_package_manager() -> str:
    for pm in PACKAGE_MANAGERS:
        if shutil.which(pm):
            return "apt" if pm == "apt-get" else pm
    if shutil.which("apt-get"):
        return "apt"
    return ""


def version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in re.split(r"[.\-]", text):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group(0)))
    return tuple(parts)


def is_older(found: str, minimum: str) -> bool:
    if not found or not minimum:
        return False
    return version_tuple(found) < version_tuple(minimum)


def _find_binary(tool: Tool) -> str:
    if tool.bundled:
        bases = []
        if os.environ.get("APPDIR"):
            bases.append(Path(os.environ["APPDIR"]) / "usr" / "bin")
        # Running from a source checkout after packaging/build-untrunc.sh
        bases.append(Path(__file__).resolve().parents[3] / "build" / "bin")
        for base in bases:
            for name in tool.binaries:
                cand = base / name
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
    search = host_env().get("PATH", "")
    if tool.extra_dirs:
        search = ":".join([search, *tool.extra_dirs])
    for name in tool.binaries:
        found = shutil.which(name, path=search)
        if found:
            return found
    return ""


def _read_version(tool: Tool) -> str:
    if not tool.version_cmd or not tool.path:
        return ""
    argv = [tool.path] + tool.version_cmd[1:]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=10,
            env=host_env(), stdin=subprocess.DEVNULL, errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m = re.search(tool.version_re, out)
    return m.group(1) if m else ""


def ffmpeg_codec_gaps(ffmpeg_path: str, wanted: dict) -> list[str]:
    """Return codecs the recipes need that this ffmpeg build lacks."""
    missing = []
    for kind in ("decoders", "encoders"):
        names = wanted.get(kind, [])
        if not names:
            continue
        try:
            proc = subprocess.run(
                [ffmpeg_path, "-hide_banner", f"-{kind}"], capture_output=True,
                text=True, timeout=15, env=host_env(), stdin=subprocess.DEVNULL, errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            return [f"{kind}: could not list"]
        have = set()
        for line in proc.stdout.splitlines():
            m = re.match(r"^\s[VASDT][.A-Z]{5}\s+(\S+)", line)
            if m:
                have.add(m.group(1))
        for name in names:
            if name not in have:
                missing.append(f"{kind[:-1]} {name}")
    return missing


@dataclass
class Report:
    tools: dict[str, Tool]
    package_manager: str
    ffmpeg_gaps: list[str]

    def available(self, key: str) -> bool:
        t = self.tools.get(key)
        return bool(t and t.path)

    def path(self, key: str) -> str:
        t = self.tools.get(key)
        return t.path if t else ""

    def group_satisfied(self, group: str) -> bool:
        return any(t.path for t in self.tools.values() if t.group == group)

    def missing_required(self) -> list[Tool]:
        out = []
        seen_groups = set()
        for t in self.tools.values():
            if not t.required or t.path:
                continue
            if t.group:
                if t.group in seen_groups or self.group_satisfied(t.group):
                    continue
                seen_groups.add(t.group)
            out.append(t)
        return out

    def missing_optional(self) -> list[Tool]:
        return [t for t in self.tools.values() if not t.required and not t.path]

    def install_command(self, tools: list[Tool]) -> str:
        """One command that installs every tool in the list for this distro."""
        pm = self.package_manager
        pkgs = []
        notes = []
        for t in tools:
            pkg = t.packages.get(pm, "")
            if t.bundled:
                notes.append(f"{t.key} ships inside the AppImage")
            elif pkg:
                if pkg not in pkgs:
                    pkgs.append(pkg)
            elif t.upstream_note:
                notes.append(f"{t.key}: {t.upstream_note}")
            else:
                notes.append(f"{t.key}: install it with your package manager")
        lines = []
        if pkgs:
            template = INSTALL_TEMPLATES.get(pm)
            if template:
                lines.append(template.format(pkgs=" ".join(pkgs)))
            else:
                lines.append(f"Install with {pm or 'your package manager'}: {' '.join(pkgs)}")
        for note in notes:
            lines.append(f"# {note}")
        return "\n".join(lines)

    def install_command_for(self, key: str) -> str:
        t = self.tools.get(key)
        if not t:
            return ""
        if t.group:
            # For a group, suggest only the first member.
            members = [m for m in self.tools.values() if m.group == t.group]
            return self.install_command(members[:1])
        return self.install_command([t])

    def everything_missing_command(self) -> str:
        wanted = self.missing_required() + [t for t in self.missing_optional() if not t.bundled]
        return self.install_command(wanted)


def scan(deps_file: Path = DEPS_FILE) -> Report:
    tools, codecs = load(deps_file)
    pm = detect_package_manager()
    gaps: list[str] = []
    for t in tools.values():
        t.path = _find_binary(t)
        if not t.path:
            t.status = MISSING
            continue
        t.version = _read_version(t)
        t.status = FOUND
        if is_older(t.version, t.min_version):
            t.status = OUTDATED
            t.detail = f"older than {t.min_version}" + (f" ({t.cve})" if t.cve else "")
    ff = tools.get("ffmpeg")
    if ff and ff.path:
        gaps = ffmpeg_codec_gaps(ff.path, codecs)
        if gaps and ff.status == FOUND:
            ff.status = LIMITED
            ff.detail = "missing " + ", ".join(gaps)
    return Report(tools=tools, package_manager=pm, ffmpeg_gaps=gaps)
