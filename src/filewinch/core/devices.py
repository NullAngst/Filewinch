"""List drives, partitions and optical drives.

lsblk --json -O gives the layout. smartctl gives health (backends/smart.py).
udevadm, when present, tells what kind of disc is in an optical drive.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from filewinch.core.jobs import host_env

# Disc kinds
DISC_NONE = ""
DISC_AUDIO = "CD-DA"
DISC_VCD = "VCD"
DISC_DATA_CD = "Data CD"
DISC_DVD = "DVD"
DISC_DVD_VIDEO = "DVD-Video"
DISC_DVD_VR = "DVD-VR"
DISC_BD = "Blu-ray"
DISC_BDAV = "BDAV/BDMV"


@dataclass
class Device:
    path: str
    name: str
    kind: str              # disk, part, rom, loop, crypt, lvm, ...
    size: int
    model: str = ""
    serial: str = ""
    transport: str = ""
    removable: bool = False
    rotational: bool = True
    discard: bool = False
    fstype: str = ""
    label: str = ""
    mountpoints: list[str] = field(default_factory=list)
    readonly: bool = False
    parent: str = ""
    children: list["Device"] = field(default_factory=list)
    disc: str = DISC_NONE
    smart: object = None   # backends.smart.Health, filled later

    @property
    def is_ssd(self) -> bool:
        return not self.rotational or self.transport == "nvme"

    @property
    def trim_warning(self) -> bool:
        """SSD with discard: deleted blocks read back as zeros."""
        return self.is_ssd and self.discard and self.kind != "rom"

    @property
    def mounted(self) -> bool:
        if self.mountpoints:
            return True
        return any(c.mounted for c in self.children)

    def all_mountpoints(self) -> list[str]:
        out = list(self.mountpoints)
        for c in self.children:
            out.extend(c.all_mountpoints())
        return out


def _int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _from_lsblk(node: dict, parent: str = "") -> Device:
    mps = [m for m in (node.get("mountpoints") or [node.get("mountpoint")]) if m]
    dev = Device(
        path=node.get("path") or f"/dev/{node.get('name', '')}",
        name=node.get("name", ""),
        kind=node.get("type", ""),
        size=_int(node.get("size")),
        model=(node.get("model") or "").strip(),
        serial=(node.get("serial") or "").strip(),
        transport=node.get("tran") or "",
        removable=bool(node.get("rm")) or bool(node.get("hotplug")),
        rotational=bool(node.get("rota")),
        discard=_int(node.get("disc-max")) > 0 and _int(node.get("disc-gran")) > 0,
        fstype=node.get("fstype") or "",
        label=node.get("label") or "",
        mountpoints=mps,
        readonly=bool(node.get("ro")),
        parent=parent,
    )
    dev.children = [_from_lsblk(c, dev.path) for c in node.get("children", []) or []]
    return dev


def parse_lsblk(text: str) -> list[Device]:
    data = json.loads(text)
    return [_from_lsblk(n) for n in data.get("blockdevices", [])]


def list_devices() -> list[Device]:
    lsblk = shutil.which("lsblk")
    if not lsblk:
        return []
    proc = subprocess.run([lsblk, "--json", "-O", "-b"], capture_output=True, text=True,
                          env=host_env(), timeout=20)
    if proc.returncode != 0:
        return []
    devices = [d for d in parse_lsblk(proc.stdout)
               if d.kind in ("disk", "rom", "loop", "crypt", "lvm", "md", "raid1")
               and not d.name.startswith(("zram", "ram"))  # compressed swap and RAM disks
               and not (d.kind == "loop" and d.size == 0)]
    for d in devices:
        if d.kind == "rom":
            d.disc = identify_disc(d)
    # Removable and optical drives first, since that is where most lost files live.
    devices.sort(key=lambda d: (0 if d.kind == "rom" else 1 if d.removable else 2, d.name))
    return devices


def udev_props(path: str) -> dict:
    udevadm = shutil.which("udevadm")
    if not udevadm:
        return {}
    proc = subprocess.run([udevadm, "info", "--query=property", f"--name={path}"],
                          capture_output=True, text=True, env=host_env(), timeout=10)
    props = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            props[k] = v
    return props


def _disc_dirs(dev: Device) -> set[str]:
    """Top-level directory names on a data disc, if we can see them unprivileged."""
    names: set[str] = set()
    for mp in dev.mountpoints:
        try:
            names.update(n.upper() for n in os.listdir(mp))
        except OSError:
            pass
    if names or not os.access(dev.path, os.R_OK):
        return names
    fls = shutil.which("fls")
    if fls:
        proc = subprocess.run([fls, dev.path], capture_output=True, text=True,
                              env=host_env(), timeout=30)
        for line in proc.stdout.splitlines():
            if "\t" in line:
                names.add(line.split("\t", 1)[1].strip().upper())
    return names


def classify_disc(props: dict, dirs: set[str]) -> str:
    if not props.get("ID_CDROM_MEDIA") and not dirs:
        return DISC_NONE
    audio = _int(props.get("ID_CDROM_MEDIA_TRACK_COUNT_AUDIO"))
    data = _int(props.get("ID_CDROM_MEDIA_TRACK_COUNT_DATA"))
    if any(k.startswith("ID_CDROM_MEDIA_BD") for k in props if props[k] == "1"):
        return DISC_BDAV if dirs & {"BDAV", "BDMV"} else DISC_BD
    if any(k.startswith("ID_CDROM_MEDIA_DVD") for k in props if props[k] == "1") or dirs & {"VIDEO_TS", "DVD_RTAV"}:
        if "DVD_RTAV" in dirs:
            return DISC_DVD_VR
        if "VIDEO_TS" in dirs:
            return DISC_DVD_VIDEO
        return DISC_DVD
    if dirs & {"VCD", "SVCD", "MPEGAV", "MPEG2"}:
        return DISC_VCD
    if audio and not data:
        return DISC_AUDIO
    return DISC_DATA_CD


def identify_disc(dev: Device) -> str:
    return classify_disc(udev_props(dev.path), _disc_dirs(dev))


def find(devices: list[Device], path: str) -> Device | None:
    for d in devices:
        if d.path == path:
            return d
        hit = find(d.children, path)
        if hit:
            return hit
    return None
