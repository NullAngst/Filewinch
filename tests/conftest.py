"""Shared test setup.

Fixtures come from tests/make-fixtures.sh, built once per test run into a
temporary folder. Set FILEWINCH_FIXTURES to a folder already built by that
script to skip the rebuild while working on the tests.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
# In CI the tests run inside the AppImage's Python against the app installed
# there (APPDIR is set). Locally they run against the checkout.
if not os.environ.get("APPDIR"):
    sys.path.insert(0, str(ROOT / "src"))

from filewinch.core import deps  # noqa: E402


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> Path:
    prebuilt = os.environ.get("FILEWINCH_FIXTURES")
    if prebuilt and (Path(prebuilt) / "repair").is_dir():
        return Path(prebuilt)
    out = tmp_path_factory.mktemp("fixtures")
    subprocess.run(["bash", str(ROOT / "tests" / "make-fixtures.sh"), str(out)], check=True)
    return out


@pytest.fixture(scope="session")
def tools() -> deps.Report:
    return deps.scan()


def need(*names: str) -> None:
    missing = [n for n in names if not shutil.which(n) and not deps.scan().available(n)]
    if missing:
        pytest.skip("missing tools: " + ", ".join(missing))


def need_codecs(report: deps.Report, *codecs: str) -> None:
    """Expected skip on distros whose stock ffmpeg leaves out these codecs."""
    gaps = set(report.ffmpeg_gaps)
    lacking = [c for c in codecs if c in gaps]
    if lacking:
        pytest.skip("this ffmpeg build lacks " + ", ".join(lacking))


def need_decoders_for(path: Path) -> None:
    """Skip when this ffmpeg cannot decode the codecs inside a fixture."""
    import json
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "json",
                            str(path)], capture_output=True, text=True)
    try:
        codecs = {s["codec_name"] for s in json.loads(probe.stdout or "{}").get("streams", [])}
    except ValueError:
        return
    listing = subprocess.run(["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True).stdout
    have = {line.split()[1] for line in listing.splitlines() if len(line.split()) > 1 and line.startswith(" ")}
    lacking = sorted(c for c in codecs if c not in have)
    if lacking:
        pytest.skip("this ffmpeg build cannot decode " + ", ".join(lacking))
