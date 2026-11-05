"""Undelete acceptance tests, on images built by make-fixtures.sh."""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path

from conftest import need

from filewinch.backends import photorec
from filewinch.core import undelete
from filewinch.core.jobs import CancelToken


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def expected(listing: Path) -> dict[str, str]:
    out = {}
    for line in listing.read_text().splitlines():
        digest, name = line.split("  ", 1)
        out[name] = digest
    return out


def carved_hashes(files: list[Path]) -> set[str]:
    return {sha(f) for f in files}


def test_fat32_named_recovery(fixtures, tmp_path):
    need("fls", "icat", "mmls", "fsstat")
    img = fixtures / "undelete" / "fat32.img"
    want = expected(fixtures / "undelete" / "fat32.deleted")
    scan = undelete.named_scan(str(img))
    assert scan.readable
    files = [e for e in scan.entries if not e.is_dir]
    names = {e.path for e in files}
    for name in want:
        assert name in names, f"{name} not listed"
    picked = [e for e in files if e.path in want]
    saved = undelete.save_named(str(img), picked, tmp_path)
    assert len(saved) == len(want)
    for name, digest in want.items():
        out = tmp_path / name
        assert out.exists(), f"{name} not saved under its original name and folder"
        assert sha(out) == digest, f"{name} hash differs"


def test_ext4_names_and_carving(fixtures, tmp_path):
    need("fls", "photorec")
    img = fixtures / "undelete" / "ext4.img"
    want = expected(fixtures / "undelete" / "ext4.deleted")
    scan = undelete.named_scan(str(img))
    names = {e.path for e in scan.entries}
    for name in want:
        assert name in names, f"{name} not listed"
    carved = undelete.carve(str(img), tmp_path / "carve")
    got = carved_hashes(carved)
    for name, digest in want.items():
        if name.endswith((".jpg", ".pdf")):
            assert digest in got, f"carving did not recover {name}"


def test_reformatted_carve(fixtures, tmp_path):
    need("photorec")
    img = fixtures / "undelete" / "reformatted.img"
    want = expected(fixtures / "undelete" / "reformatted.expected")
    carved = undelete.carve(str(img), tmp_path / "carve")
    exts = {f.suffix.lower() for f in carved}
    for ext in (".jpg", ".png", ".pdf", ".mp4"):
        assert ext in exts, f"no {ext} carved"
    got = carved_hashes(carved)
    for name, digest in want.items():
        if not name.endswith(".mp4"):
            # PhotoRec may pad an MP4 to a cluster boundary, so MP4 is checked by type only.
            assert digest in got, f"{name} not recovered intact"


def test_carve_resume_matches_uninterrupted(fixtures, tmp_path):
    need("photorec")
    img = fixtures / "undelete" / "resume.img"
    whole = undelete.carve(str(img), tmp_path / "whole")
    reference = carved_hashes(whole)
    assert reference

    out = tmp_path / "interrupted"
    token = CancelToken()
    worker = threading.Thread(target=undelete.carve, args=(str(img), out), kwargs={"cancel": token})
    worker.start()
    prefix = out / "recup_dir"
    deadline = time.time() + 120
    while time.time() < deadline and worker.is_alive():
        if photorec.carved_files(prefix):
            break
        time.sleep(0.05)
    token.cancel()
    worker.join()
    first = carved_hashes(photorec.carved_files(prefix))
    assert first != reference or not photorec.has_session(out / ".photorec-session"), \
        "the carve finished before it could be interrupted"
    resumed = undelete.carve(str(img), out)
    assert carved_hashes(resumed) == reference
    assert not photorec.has_session(out / ".photorec-session")
