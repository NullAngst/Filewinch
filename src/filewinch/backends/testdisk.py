"""TestDisk: interactive, in a terminal, always on one device.

FileWinch never drives TestDisk's menus. Automated partition writes are how
drives get wrecked. This module builds the command and reads what it left.
"""

from __future__ import annotations

from pathlib import Path

from filewinch.core import terminal


def partition_repair_argv(device: str, log_dir: Path) -> list[str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    return terminal.testdisk_argv(device, log_dir)


def undelete_argv(image: str, log_dir: Path) -> list[str]:
    """The TestDisk undelete fallback: Advanced > Undelete, run by the user on an image."""
    log_dir.mkdir(parents=True, exist_ok=True)
    return terminal.testdisk_argv("", log_dir, target=image)


def logs(log_dir: Path) -> str:
    return terminal.collect_logs(log_dir)
