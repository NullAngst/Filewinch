"""Root access, for raw device reads only.

Only the commands in WHITELIST are ever handed to pkexec or sudo. Everything
else, including everything run against an image file, runs as the user.

The default undelete flow images the device once as root with ddrescue, then
works on the image unprivileged. ddrescue writes into an image file and mapfile
the user created first, so they stay owned by the user. Tools that create their
own output files as root (cdparanoia, vcdxrip) get a chown back to the user,
restricted to the job folder FileWinch made for them.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

WHITELIST = frozenset({
    "smartctl",
    "ddrescue",
    "cdparanoia",
    "vcdxrip",
    "sfdisk",       # --dump only
    "testdisk",
    "photorec",
    "fls", "icat", "mmls", "fsstat",   # Sleuth Kit device reads
    "chown",        # job output back to the user, job folders only
})

ASKPASS_HELPERS = ("ksshaskpass", "ssh-askpass", "lxqt-openssh-askpass", "x11-ssh-askpass")


class PrivilegeError(Exception):
    pass


def _check_sfdisk(args: list[str]) -> None:
    if len(args) != 2 or args[0] != "--dump" or not args[1].startswith("/dev/"):
        raise PrivilegeError("sfdisk may only run as root with --dump /dev/<disk>")


def _check_chown(args: list[str], allowed_roots: list[str]) -> None:
    rest = list(args)
    if rest and rest[0] == "-R":
        rest = rest[1:]
    if rest and rest[0] == "--":
        rest = rest[1:]
    if len(rest) < 2 or not re.fullmatch(r"\d+:\d+", rest[0]):
        raise PrivilegeError("chown must be: chown [-R] -- UID:GID PATH...")
    uid = int(rest[0].split(":")[0])
    if uid == 0:
        raise PrivilegeError("chown to root is never allowed")
    if uid != os.getuid():
        raise PrivilegeError("chown may only hand files back to the current user")
    roots = [os.path.realpath(r) for r in allowed_roots]
    if not roots:
        raise PrivilegeError("chown needs a job folder to stay inside")
    for p in rest[1:]:
        real = os.path.realpath(p)
        if not any(real == r or real.startswith(r + os.sep) for r in roots):
            raise PrivilegeError(f"chown target {p} is outside the job folder")


def validate(argv: list[str], allowed_roots: list[str] | None = None) -> None:
    if not argv:
        raise PrivilegeError("empty command")
    name = os.path.basename(argv[0])
    if name not in WHITELIST:
        raise PrivilegeError(f"{name} is not allowed to run as root")
    if name == "sfdisk":
        _check_sfdisk(argv[1:])
    if name == "chown":
        _check_chown(argv[1:], allowed_roots or [])


def escalator() -> list[str]:
    """The prefix that runs a command as root: pkexec, else sudo."""
    if shutil.which("pkexec"):
        return [shutil.which("pkexec")]
    sudo = shutil.which("sudo")
    if sudo:
        if os.environ.get("SUDO_ASKPASS"):
            return [sudo, "-A", "--"]
        for helper in ASKPASS_HELPERS:
            path = shutil.which(helper)
            if path:
                os.environ["SUDO_ASKPASS"] = path
                return [sudo, "-A", "--"]
        # No way to ask for a password from a GUI. Works only with cached
        # credentials or NOPASSWD; the job log says so if it fails.
        return [sudo, "-n", "--"]
    raise PrivilegeError("neither pkexec nor sudo is installed")


def as_root(argv: list[str], allowed_roots: list[str] | None = None) -> list[str]:
    """Return the full argv that runs argv as root, after checking it.

    The tool is called by its real host path, so pkexec never has to read
    anything from the AppImage's FUSE mount.
    """
    validate(argv, allowed_roots)
    real = shutil.which(argv[0]) if not os.path.isabs(argv[0]) else argv[0]
    if not real:
        raise PrivilegeError(f"{argv[0]} is not installed")
    real = str(Path(real).resolve())
    if os.environ.get("APPDIR") and real.startswith(os.environ["APPDIR"]):
        raise PrivilegeError("bundled tools never run as root")
    return escalator() + [real] + list(argv[1:])


def chown_back(paths: list[str], job_root: str) -> list[str]:
    ids = f"{os.getuid()}:{os.getgid()}"
    return as_root(["chown", "-R", "--", ids, *paths], allowed_roots=[job_root])


def needs_root(path: str) -> bool:
    """True when reading this source needs root (a block device we cannot open)."""
    try:
        st = os.stat(path)
    except OSError:
        return True
    import stat as _stat
    if not _stat.S_ISBLK(st.st_mode):
        return False
    return not os.access(path, os.R_OK)
