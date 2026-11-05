"""The write guard, read-only checks and partition table backup.

Rules enforced here (from the build document):
1. Output must be on a different block device than the source. No override.
2. A source mounted read-write gets offered an unmount or read-only remount.
3. Repairs never touch the original; SHA-256 before and after.
4. Image-first by default. Direct scans are forced off when SMART reports
   pending or reallocated sectors.
5. Imaging needs free space at least the size of the source.
6. Partition repair needs a successful table backup and no mounted partition.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import re
import shutil
import stat
from pathlib import Path

from filewinch.core import privilege
from filewinch.core.jobs import run_command

PARTITION_BACKUP_DIR = Path.home() / ".local" / "share" / "filewinch" / "partition-backups"


class SafetyError(Exception):
    pass


# --- block device resolution -------------------------------------------------

def _sys_block(majmin: str) -> Path | None:
    p = Path("/sys/dev/block") / majmin
    return p.resolve() if p.exists() else None


def _disks_of_sysdir(sysdir: Path) -> set[str]:
    """Whole disks under a /sys block entry, following dm/md/loop slaves."""
    if (sysdir / "partition").exists():
        sysdir = sysdir.parent
    slaves = sysdir / "slaves"
    found: set[str] = set()
    if slaves.is_dir():
        for s in slaves.iterdir():
            found |= _disks_of_sysdir(s.resolve())
    loop_backing = sysdir / "loop" / "backing_file"
    if loop_backing.exists():
        try:
            found |= underlying_disks(loop_backing.read_text().strip())
        except (OSError, SafetyError):
            pass
    if not found:
        found.add(sysdir.name)
    return found


def _majmin(dev: int) -> str:
    return f"{os.major(dev)}:{os.minor(dev)}"


def _mount_for(path: str) -> tuple[str, str] | None:
    """(major:minor, source) of the mount holding path, from mountinfo."""
    real = os.path.realpath(path)
    best = None
    try:
        with open("/proc/self/mountinfo", encoding="utf-8") as fh:
            for line in fh:
                left, _, right = line.partition(" - ")
                fields = left.split()
                if len(fields) < 5:
                    continue
                mnt = fields[4].replace("\\040", " ")
                if real == mnt or real.startswith(mnt.rstrip("/") + "/") or mnt == "/":
                    if best is None or len(mnt) >= len(best[0]):
                        src = right.split()[1] if len(right.split()) > 1 else ""
                        best = (mnt, fields[2], src)
    except OSError:
        return None
    return (best[1], best[2]) if best else None


def underlying_disks(path: str) -> set[str]:
    """A set of tokens naming the physical disks a path lives on.

    For a block device: its whole disk(s). For a file or folder: the disk(s)
    under the filesystem holding it. Filesystems with no block device behind
    them (tmpfs, overlay) get an 'anon:' token from their device number.
    """
    p = path
    while not os.path.exists(p):
        parent = os.path.dirname(p)
        if parent == p:
            raise SafetyError(f"{path} does not exist")
        p = parent
    st = os.stat(p)
    if stat.S_ISBLK(st.st_mode):
        sysdir = _sys_block(_majmin(st.st_rdev))
        return _disks_of_sysdir(sysdir) if sysdir else {f"blk:{_majmin(st.st_rdev)}"}
    sysdir = _sys_block(_majmin(st.st_dev))
    if sysdir:
        return _disks_of_sysdir(sysdir)
    # btrfs and friends report an anonymous st_dev; the mount source is real.
    mount = _mount_for(p)
    if mount and mount[1].startswith("/dev/"):
        try:
            return underlying_disks(mount[1])
        except (OSError, SafetyError):
            pass
    return {f"anon:{_majmin(st.st_dev)}"}


def check_output_location(source: str, output_dir: str) -> None:
    """Rule 1. Raises SafetyError when output and source share a disk."""
    src = underlying_disks(source)
    out = underlying_disks(output_dir)
    shared = src & out
    if shared:
        raise SafetyError(
            "The output folder is on the same drive as the source "
            f"({', '.join(sorted(shared))}). Writing there can overwrite the very "
            "files you are trying to recover. Pick a folder on another drive."
        )


# --- mount state -------------------------------------------------------------

def rw_mounts(device) -> list[tuple[str, str]]:
    """(device node, mountpoint) for a device or its partitions mounted read-write."""
    paths = {device.path} | {c.path for c in device.children}
    for c in device.children:
        paths |= {g.path for g in c.children}
    hits = []
    try:
        with open("/proc/self/mounts", encoding="utf-8") as fh:
            for line in fh:
                parts = line.split()
                if len(parts) < 4:
                    continue
                src = os.path.realpath(parts[0]) if parts[0].startswith("/dev/") else parts[0]
                if src in paths and "rw" in parts[3].split(","):
                    hits.append((src, parts[1].replace("\\040", " ")))
    except OSError:
        pass
    return hits


def unmount_command(node: str, mountpoint: str) -> list[str]:
    """udisksctl first (no root needed for removable media), plain umount otherwise.

    Neither goes through pkexec: unmounting is not on the root whitelist. If
    the desktop's polkit rules refuse, the user unmounts it themselves.
    """
    udisks = shutil.which("udisksctl")
    if udisks:
        return [udisks, "unmount", "--block-device", node]
    return [shutil.which("umount") or "umount", mountpoint]


def remount_ro_command(mountpoint: str) -> str:
    """Shown to the user to run, since mount is not on the root whitelist."""
    return f"sudo mount -o remount,ro {mountpoint}"


# --- space and health ----------------------------------------------------------

def free_space_ok(dest_dir: str, needed: int) -> tuple[bool, int]:
    usage = shutil.disk_usage(dest_dir)
    return usage.free >= needed, usage.free


def direct_scan_allowed(health) -> tuple[bool, str]:
    """Rule 4. health is a backends.smart.Health or None."""
    if health is None:
        return True, ""
    if health.pending or health.reallocated:
        return False, (f"SMART reports {health.pending} pending and {health.reallocated} "
                       "reallocated sectors. Every extra read of a dying drive can be "
                       "its last, so this drive gets imaged first.")
    return True, ""


# --- hashes ---------------------------------------------------------------------

def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- partition table backup -------------------------------------------------------

def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unknown"


def backup_path(model: str, serial: str, when: _dt.datetime | None = None) -> Path:
    when = when or _dt.datetime.now()
    name = f"{_safe_name(model)}-{_safe_name(serial)}-{when:%Y%m%d-%H%M%S}.sfdisk"
    return PARTITION_BACKUP_DIR / name


def dump_partition_table(device: str, dest: Path, log=None, root: bool = True) -> Path:
    """sfdisk --dump into dest. Raises SafetyError when it fails or is empty."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = ["sfdisk", "--dump", device]
    if root:
        argv = privilege.as_root(argv)
    res = run_command(argv, log=log, stdout_file=str(dest))
    if not res.ok or not dest.exists() or dest.stat().st_size == 0:
        try:
            dest.unlink()
        except OSError:
            pass
        raise SafetyError(f"sfdisk --dump failed ({res.stderr.strip() or res.code}). "
                          "Partition repair will not start without a backup.")
    return dest


def restore_command(device: str, backup: Path) -> str:
    return f"sudo sfdisk {device} < {backup}"


def partition_repair_preflight(device, backup: Path | None) -> None:
    """Rule 6."""
    if backup is None or not Path(backup).exists() or Path(backup).stat().st_size == 0:
        raise SafetyError("No partition table backup. Partition repair refuses to start.")
    mounted = device.all_mountpoints() if hasattr(device, "all_mountpoints") else []
    if mounted:
        raise SafetyError("A partition on this drive is mounted at "
                          f"{', '.join(mounted)}. Unmount it first.")


def diff_tables(before: str, after: str) -> list[str]:
    import difflib
    return list(difflib.unified_diff(before.splitlines(), after.splitlines(),
                                     "before", "after", lineterm=""))
