"""Dependency detection tests."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from filewinch.core import deps

ROOT = Path(__file__).resolve().parent.parent


def test_deps_toml_loads_and_is_complete():
    tools, codecs = deps.load()
    for t in tools.values():
        assert t.feature, t.key
        assert set(t.packages) == {"apt", "dnf", "zypper", "pacman"}, t.key
    assert codecs["encoders"] and codecs["decoders"]
    # The documented required tools.
    for key in ("lsblk", "sfdisk", "smartctl", "ddrescue", "testdisk", "photorec", "fidentify",
                "fls", "icat", "file", "ffmpeg", "ffprobe"):
        assert tools[key].required, key


def test_only_untrunc_is_bundled():
    tools, _ = deps.load()
    assert [t.key for t in tools.values() if t.bundled] == ["untrunc"]


def test_version_compare():
    assert deps.is_older("4.12.1", "4.15.0")
    assert not deps.is_older("4.15.0", "4.15.0")
    assert not deps.is_older("103.0", "102.0")
    assert deps.is_older("101.0.1", "102.0")
    assert not deps.is_older("", "1.0")


def _fake_bin(dir_: Path, name: str, output: str) -> None:
    p = dir_ / name
    p.write_text(f"#!/bin/sh\nprintf '%s\\n' '{output}'\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)


def test_outdated_tool_flagged(tmp_path, monkeypatch):
    _fake_bin(tmp_path, "mkvmerge", "mkvmerge v99.0 ('Old') 64-bit")
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    report = deps.scan()
    t = report.tools["mkvmerge"]
    assert t.version == "99.0"
    assert t.status == deps.OUTDATED
    assert "CVE-2026-90783" in t.detail


def _path_without(names: set[str], tmp_path: Path) -> str:
    """A PATH holding symlinks to every host tool except the named ones."""
    shim = tmp_path / "bin"
    shim.mkdir()
    for d in os.environ["PATH"].split(":"):
        if not os.path.isdir(d):
            continue
        for entry in os.listdir(d):
            if entry in names or (shim / entry).exists():
                continue
            full = os.path.join(d, entry)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                (shim / entry).symlink_to(full)
    return str(shim)


def test_missing_required_tool_gets_install_command(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _path_without({"ddrescue"}, tmp_path))
    report = deps.scan()
    assert report.tools["ddrescue"].status == deps.MISSING
    missing = [t.key for t in report.missing_required()]
    assert "ddrescue" in missing
    cmd = report.install_command_for("ddrescue")
    if report.package_manager in deps.INSTALL_TEMPLATES:
        assert report.tools["ddrescue"].packages[report.package_manager] in cmd
        assert cmd.startswith("sudo ")
    assert "ddrescue" in report.everything_missing_command() or report.package_manager == ""


def test_missing_optional_tool_greys_feature(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _path_without({"qpdf"}, tmp_path))
    report = deps.scan()
    assert report.tools["qpdf"].status == deps.MISSING
    assert "qpdf" not in [t.key for t in report.missing_required()]
    from filewinch.repair import recipes
    from filewinch.repair.common import Ctx
    ctx = Ctx(src=Path("x.pdf"), work=tmp_path, tools=report)
    ctx.tool = lambda k: report.path(k)  # only what the scan found
    pdf = recipes.for_ext("pdf")
    qpdf_recipe = pdf.recipes(ctx)[0]
    from filewinch.repair.engine import _missing_tools
    assert _missing_tools(ctx, qpdf_recipe) == ["qpdf"]


def test_install_command_templates(monkeypatch):
    tools, _ = deps.load()
    for pm, tmpl in deps.INSTALL_TEMPLATES.items():
        r = deps.Report(tools=tools, package_manager=pm, ffmpeg_gaps=[])
        cmd = r.install_command([tools["ddrescue"], tools["fls"], tools["icat"]])
        first = cmd.splitlines()[0]
        assert first.startswith(tmpl.split("{")[0])
        # sleuthkit appears once even though two tools need it
        assert first.split().count(tools["fls"].packages[pm]) == 1


def test_tsduck_gets_upstream_note():
    tools, _ = deps.load()
    r = deps.Report(tools=tools, package_manager="apt", ffmpeg_gaps=[])
    assert "github.com/tsduck" in r.install_command([tools["tsanalyze"]])


def test_app_opens_on_dependencies_when_required_missing(tmp_path, monkeypatch):
    pytest.importorskip("PyQt6")
    monkeypatch.setenv("PATH", _path_without({"ddrescue"}, tmp_path))
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    prefix = "" if os.environ.get("APPDIR") else "import sys; sys.path.insert(0, %r)\n" % str(ROOT / "src")
    code = (prefix +
            "from filewinch.ui.main_window import build_for_test\n"
            "w = build_for_test()\n"
            "print(w.current_page_name())\n")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                         env={**os.environ, "HOME": str(tmp_path)})
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip().splitlines()[-1] == "dependencies"


def test_install_deps_script_is_shellcheck_clean():
    if not shutil.which("shellcheck"):
        pytest.skip("shellcheck missing")
    for script in list((ROOT / "tests").glob("*.sh")) + list((ROOT / "packaging").glob("*.sh")):
        res = subprocess.run(["shellcheck", str(script)], capture_output=True, text=True)
        assert res.returncode == 0, res.stdout
