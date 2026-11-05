"""The Sleuth Kit: partition layout, filesystem type, deleted names, file contents."""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from filewinch.core import privilege
from filewinch.core.jobs import run_command

MMLS_RE = re.compile(r"^\s*\d+:\s+(\S+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)$")


@dataclass
class Volume:
    offset: int          # in sectors, for -o
    length: int
    description: str
    order: int           # PhotoRec-style partition number, 1-based


@dataclass
class Entry:
    path: str            # original path inside the filesystem
    inode: str           # inode or NTFS MFT address
    size: int
    mtime: int
    crtime: int
    is_dir: bool
    offset: int = 0      # volume offset in sectors
    volume: str = ""


def _tool(name: str) -> str:
    return shutil.which(name) or name


def _maybe_root(cmd: list[str], root: bool) -> list[str]:
    return privilege.as_root([Path(cmd[0]).name, *cmd[1:]]) if root else cmd


def volumes(source: str, root: bool = False) -> list[Volume]:
    """Partitions inside a disk image. A bare filesystem image gives one volume at 0."""
    res = run_command(_maybe_root([_tool("mmls"), source], root))
    vols = []
    if res.ok:
        order = 0
        for line in res.stdout.splitlines():
            m = MMLS_RE.match(line)
            if not m:
                continue
            slot, start, _end, length, desc = m.groups()
            if slot.startswith("Meta") or "Unallocated" in desc or "Primary Table" in desc \
                    or "Extended" in desc or "Safety" in desc or slot == "-------":
                continue
            order += 1
            vols.append(Volume(int(start), int(length), desc.strip(), order))
    return vols or [Volume(0, 0, "whole source", 1)]


def fstype(source: str, offset: int = 0, root: bool = False) -> str:
    cmd = [_tool("fsstat")] + (["-o", str(offset)] if offset else []) + [source]
    res = run_command(_maybe_root(cmd, root))
    if not res.ok:
        return ""
    m = re.search(r"File System Type:\s*(.+)", res.stdout)
    return m.group(1).strip() if m else ""


def parse_body(text: str, offset: int = 0, volume: str = "") -> list[Entry]:
    """Parse fls -m body format: MD5|name|inode|mode|uid|gid|size|atime|mtime|ctime|crtime."""
    out = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 11:
            continue
        name = parts[1]
        name = re.sub(r" \(deleted(-realloc)?\)$", "", name)
        if name.endswith("/$OrphanFiles") or name in ("/.", "/.."):
            continue
        try:
            size = int(parts[6])
            mtime = int(parts[8])
            crtime = int(parts[10])
        except ValueError:
            size = mtime = crtime = 0
        mode = parts[3]
        out.append(Entry(path=name.lstrip("/"), inode=parts[2], size=size, mtime=mtime,
                         crtime=crtime, is_dir=mode.startswith("d"), offset=offset, volume=volume))
    return out


def list_deleted(source: str, offset: int = 0, root: bool = False, log=None, cancel=None,
                 deleted_only: bool = True) -> list[Entry]:
    """fls body listing. deleted_only=False lists every file (used to pull VOBs off a disc image)."""
    cmd = [_tool("fls"), "-r"] + (["-d"] if deleted_only else []) + ["-p", "-m", "/"] + (["-o", str(offset)] if offset else []) + [source]
    res = run_command(_maybe_root(cmd, root), log=log, cancel=cancel)
    if res.code not in (0, 1) or not res.stdout:
        return []
    return parse_body(res.stdout, offset)


def icat_argv(source: str, entry: Entry, root: bool = False) -> list[str]:
    cmd = [_tool("icat"), "-r"] + (["-o", str(entry.offset)] if entry.offset else []) + [source, entry.inode]
    return _maybe_root(cmd, root)


def save(source: str, entry: Entry, dest_root: Path, root: bool = False, log=None, cancel=None) -> Path | None:
    """Write one deleted file into dest_root, keeping its folder path."""
    rel = Path(entry.path)
    if rel.is_absolute() or ".." in rel.parts:
        rel = Path(*[p for p in rel.parts if p not in ("..", "/")])
    dest = dest_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stem, n = dest.stem, 1
        while dest.exists():
            dest = dest.with_name(f"{stem}.{n}{dest.suffix}")
            n += 1
    res = run_command(icat_argv(source, entry, root), log=log, cancel=cancel, stdout_file=str(dest))
    if not res.ok:
        if dest.exists() and dest.stat().st_size == 0:
            dest.unlink()
        return None
    return dest
