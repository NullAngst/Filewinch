"""Open TestDisk in a terminal and wait for it.

TestDisk is driven by the user, never automated. FileWinch finds a terminal,
runs TestDisk as root on one device inside it, and waits for the window to
close.

Logging: TestDisk's /log writes testdisk.log into its working directory, and
pkexec starts programs in root's home, so that file can land where the user
cannot read it. The whole session is therefore also recorded with script(1)
into a log the user owns. The before/after sfdisk diff is the authoritative
record of what changed.
"""

from __future__ import annotations

import shlex
import shutil
from pathlib import Path

from filewinch.core import privilege

# (binary, argv prefix that runs a command and blocks until it exits)
TERMINALS = [
    ("konsole", ["--nofork", "-e"]),
    ("gnome-terminal", ["--wait", "--"]),
    ("xfce4-terminal", ["--disable-server", "-x"]),
    ("kitty", []),
    ("alacritty", ["-e"]),
    ("xterm", ["-e"]),
]


def find_terminal() -> tuple[str, list[str]] | None:
    for name, prefix in TERMINALS:
        path = shutil.which(name)
        if path:
            return path, prefix
    return None


def testdisk_argv(device: str, log_dir: Path, target: str = "") -> list[str]:
    """Full argv for the terminal. target lets undelete open TestDisk on an image."""
    term = find_terminal()
    if not term:
        raise RuntimeError("No terminal emulator found. Install konsole, xterm, or whichever you prefer.")
    path, prefix = term
    source = target or device
    if source.startswith("/dev/"):
        inner = privilege.as_root(["testdisk", "/log", source])
    else:
        inner = [shutil.which("testdisk") or "testdisk", "/log", source]
    session_log = log_dir / "testdisk-session.log"
    script = shutil.which("script")
    run = " ".join(shlex.quote(a) for a in inner)
    if script:
        run = (f"{shlex.quote(script)} -q -e -c {shlex.quote(run)} "
               f"{shlex.quote(str(session_log))}")
    shell = (f"cd {shlex.quote(str(log_dir))} || exit 1; {run}; "
             "printf '\\nTestDisk has exited. Press Enter to close this window.'; read -r _")
    return [path, *prefix, "sh", "-c", shell]


def collect_logs(log_dir: Path) -> str:
    """Whatever TestDisk logs we can read, newest first."""
    texts = []
    for name in ("testdisk.log", "testdisk-session.log"):
        p = log_dir / name
        try:
            texts.append(f"== {name} ==\n" + p.read_text(errors="replace"))
        except OSError:
            continue
    return "\n\n".join(texts)
