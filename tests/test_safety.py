"""Safety acceptance tests. IF ANY OF THESE FAIL, DO NOT RELEASE."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from filewinch.core import privilege, safety


def _block_device_of(path: str) -> str | None:
    """The /dev node of the disk under path, when the container exposes one."""
    disks = safety.underlying_disks(path)
    for d in disks:
        node = Path("/dev") / d
        if node.exists():
            return str(node)
    return None


# --- rule 1: output on the source drive ------------------------------------------

def test_output_on_same_drive_refused_for_image(tmp_path):
    image = tmp_path / "source.img"
    image.write_bytes(b"\0" * 4096)
    out = tmp_path / "recovered"
    out.mkdir()
    with pytest.raises(safety.SafetyError):
        safety.check_output_location(str(image), str(out))


def test_output_on_same_drive_refused_for_device(tmp_path):
    dev = _block_device_of(str(tmp_path))
    if not dev:
        pytest.skip("no block device node behind the test folder in this container")
    with pytest.raises(safety.SafetyError):
        safety.check_output_location(dev, str(tmp_path))


def test_output_folder_not_created_yet_still_checked(tmp_path):
    image = tmp_path / "source.img"
    image.write_bytes(b"\0" * 4096)
    with pytest.raises(safety.SafetyError):
        safety.check_output_location(str(image), str(tmp_path / "does" / "not" / "exist"))


def test_output_on_another_drive_allowed(tmp_path):
    image = tmp_path / "source.img"
    image.write_bytes(b"\0" * 4096)
    src = safety.underlying_disks(str(image))
    for cand in ("/dev/shm", "/run", "/opt", "/mnt", "/var/tmp"):
        if os.path.isdir(cand) and os.access(cand, os.W_OK) and not (safety.underlying_disks(cand) & src):
            safety.check_output_location(str(image), cand)
            return
    pytest.skip("no writable folder on a second drive in this container")


def test_no_override_exists():
    # Rule 1 has no override. Nothing in safety may take a force flag.
    import inspect
    sig = inspect.signature(safety.check_output_location)
    assert list(sig.parameters) == ["source", "output_dir"]


# --- whitelist ------------------------------------------------------------------

def test_whitelist_is_exactly_the_documented_set():
    assert privilege.WHITELIST == {
        "smartctl", "ddrescue", "cdparanoia", "vcdxrip", "sfdisk", "testdisk", "photorec",
        "fls", "icat", "mmls", "fsstat", "chown",
    }


@pytest.mark.parametrize("argv", [
    ["sh", "-c", "id"], ["bash"], ["rm", "-rf", "/"], ["dd", "if=/dev/zero", "of=/dev/sda"],
    ["ffmpeg", "-i", "x"], ["mkfs.ext4", "/dev/sda"], ["python3"], ["/usr/bin/env", "smartctl"],
])
def test_non_whitelisted_commands_refused(argv):
    with pytest.raises(privilege.PrivilegeError):
        privilege.validate(argv)


@pytest.mark.parametrize("args", [
    ["/dev/sda"], ["--delete", "/dev/sda"], ["--dump", "/tmp/x"], ["--dump", "/dev/sda", "extra"], [],
])
def test_sfdisk_only_dump(args):
    with pytest.raises(privilege.PrivilegeError):
        privilege.validate(["sfdisk", *args])


def test_sfdisk_dump_allowed():
    privilege.validate(["sfdisk", "--dump", "/dev/sdb"])


def test_chown_stays_in_job_folder(tmp_path, monkeypatch):
    # CI containers run as root; pretend to be an ordinary user like the real app.
    monkeypatch.setattr(privilege.os, "getuid", lambda: 1000)
    ids = "1000:1000"
    job = tmp_path / "job"
    job.mkdir()
    privilege.validate(["chown", "-R", "--", ids, str(job / "track01.wav")], allowed_roots=[str(job)])
    with pytest.raises(privilege.PrivilegeError):
        privilege.validate(["chown", "-R", "--", ids, "/etc/passwd"], allowed_roots=[str(job)])
    with pytest.raises(privilege.PrivilegeError):
        privilege.validate(["chown", "-R", "--", "0:0", str(job)], allowed_roots=[str(job)])
    with pytest.raises(privilege.PrivilegeError):
        privilege.validate(["chown", "-R", "--", ids, str(job / ".." / "elsewhere")], allowed_roots=[str(job)])


def test_as_root_never_wraps_anything_else(monkeypatch):
    monkeypatch.setattr(privilege, "escalator", lambda: ["/usr/bin/pkexec"])
    monkeypatch.setattr(privilege.shutil, "which", lambda name, *a, **k: f"/usr/bin/{name}")
    cmd = privilege.as_root(["smartctl", "-H", "-j", "/dev/sda"])
    assert cmd[0] == "/usr/bin/pkexec" and Path(cmd[1]).name == "smartctl"
    with pytest.raises(privilege.PrivilegeError):
        privilege.as_root(["cat", "/etc/shadow"])


def test_every_root_call_site_goes_through_the_whitelist():
    """Every as_root() call in the source names a whitelisted tool."""
    import re
    src = Path(__file__).resolve().parent.parent / "src" / "filewinch"
    for py in src.rglob("*.py"):
        text = py.read_text()
        for m in re.finditer(r'as_root\(\[\s*"([^"]+)"', text):
            assert m.group(1) in privilege.WHITELIST, f"{py.name} escalates {m.group(1)}"
    # And nothing calls pkexec or sudo directly.
    for py in src.rglob("*.py"):
        if py.name == "privilege.py":
            continue
        assert '"pkexec"' not in py.read_text() and '"sudo"' not in py.read_text(), py.name


# --- rule 6: partition repair ------------------------------------------------------

class FakeDevice:
    def __init__(self, mounts):
        self._mounts = mounts

    def all_mountpoints(self):
        return list(self._mounts)


def test_partition_repair_refused_without_backup(tmp_path):
    with pytest.raises(safety.SafetyError):
        safety.partition_repair_preflight(FakeDevice([]), None)
    empty = tmp_path / "empty.sfdisk"
    empty.write_text("")
    with pytest.raises(safety.SafetyError):
        safety.partition_repair_preflight(FakeDevice([]), empty)


def test_partition_repair_refused_when_mounted(tmp_path):
    backup = tmp_path / "ok.sfdisk"
    backup.write_text("label: gpt\n")
    with pytest.raises(safety.SafetyError):
        safety.partition_repair_preflight(FakeDevice(["/media/stick"]), backup)
    safety.partition_repair_preflight(FakeDevice([]), backup)


def test_failed_sfdisk_dump_raises_and_leaves_no_backup(tmp_path):
    dest = tmp_path / "backup.sfdisk"
    with pytest.raises(safety.SafetyError):
        # Unprivileged dump of a path that is not a disk fails like a real failure would.
        safety.dump_partition_table(str(tmp_path / "no-such-disk"), dest, root=False)
    assert not dest.exists()


def test_successful_dump_on_image(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("sfdisk"):
        pytest.skip("sfdisk missing")
    img = tmp_path / "disk.img"
    img.write_bytes(b"\0" * (8 * 1024 * 1024))
    subprocess.run(["sfdisk", "--quiet", str(img)], input="label: dos\n,4MiB,L\n", text=True, check=True)
    dest = tmp_path / "backup.sfdisk"
    safety.dump_partition_table(str(img), dest, root=False)
    assert "label: dos" in dest.read_text()
    safety.partition_repair_preflight(FakeDevice([]), dest)


def test_backup_path_layout():
    import datetime as dt
    p = safety.backup_path("SanDisk Ultra", "4C53/0001", dt.datetime(2026, 11, 1, 9, 5, 0))
    assert p.parent.parts[-3:] == ("filewinch", "partition-backups") or p.parent.name == "partition-backups"
    assert p.name == "SanDisk_Ultra-4C53_0001-20261101-090500.sfdisk"


# --- rule 4 ---------------------------------------------------------------------

def test_direct_scan_forced_off_on_bad_smart():
    from filewinch.backends.smart import Health
    ok, _ = safety.direct_scan_allowed(Health(verdict="ok"))
    assert ok
    ok, why = safety.direct_scan_allowed(Health(verdict="warning", pending=3))
    assert not ok and "pending" in why
    ok, _ = safety.direct_scan_allowed(Health(verdict="warning", reallocated=1))
    assert not ok
