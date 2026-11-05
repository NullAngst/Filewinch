"""smartctl: is this drive healthy enough to scan directly?"""

from __future__ import annotations

import json
from dataclasses import dataclass

from filewinch.core import privilege
from filewinch.core.jobs import run_command

UNKNOWN = "unknown"
OK = "ok"
WARN = "warning"
FAILING = "failing"


@dataclass
class Health:
    verdict: str = UNKNOWN
    passed: bool | None = None
    pending: int = 0
    reallocated: int = 0
    uncorrectable: int = 0
    note: str = ""

    def text(self) -> str:
        if self.verdict == UNKNOWN:
            return "No SMART data" + (f" ({self.note})" if self.note else "")
        bits = {OK: "Healthy", WARN: "Warning", FAILING: "Failing"}[self.verdict]
        if self.pending or self.reallocated:
            bits += f": {self.pending} pending, {self.reallocated} reallocated"
        return bits


def argv(device: str) -> list[str]:
    return privilege.as_root(["smartctl", "-H", "-A", "-j", device])


def parse(text: str) -> Health:
    h = Health()
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        h.note = "smartctl gave no JSON"
        return h
    status = data.get("smart_status", {})
    if "passed" in status:
        h.passed = bool(status["passed"])
    for row in data.get("ata_smart_attributes", {}).get("table", []):
        raw = row.get("raw", {}).get("value", 0) or 0
        if row.get("id") == 5:
            h.reallocated = int(raw)
        elif row.get("id") == 197:
            h.pending = int(raw)
        elif row.get("id") == 198:
            h.uncorrectable = int(raw)
    nvme = data.get("nvme_smart_health_information_log")
    if nvme:
        h.uncorrectable = int(nvme.get("media_errors", 0) or 0)
    if h.passed is None and not data.get("ata_smart_attributes") and not nvme:
        msgs = data.get("smartctl", {}).get("messages", [])
        h.note = msgs[0].get("string", "") if msgs else "not supported by this device"
        return h
    if h.passed is False:
        h.verdict = FAILING
    elif h.pending or h.reallocated or h.uncorrectable:
        h.verdict = WARN
    else:
        h.verdict = OK
    return h


def check(device: str, log=None) -> Health:
    try:
        cmd = argv(device)
    except privilege.PrivilegeError as exc:
        return Health(note=str(exc))
    res = run_command(cmd, log=log, timeout=60)
    return parse(res.stdout)
