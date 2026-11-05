# Changes

## 2026.11.05 - Bug fixes

- Decode check runs video and audio as separate passes, each timing its own
frames (showinfo and ashowinfo), so audio damage is placed correctly on
ffmpeg 8, which decodes audio on its own thread.
- Each pass ignores errors from the other kind of stream. ffmpeg 8 runs the
Opus packet parser for every stream, so Opus errors showed up in the video
pass too.
- Audio decoder errors in the final second of a stream are ignored. ffmpeg
8.0.1 reports "Error parsing Opus packet header" on the last packet of
healthy Opus-in-Matroska files, which made every such file look damaged and
cost the GOP cut an extra second. Real damage in the last second of a
soundtrack now goes unreported.
- ffmpeg log tags like "[vist#0:0/h264 @" and "[dec:h264 @" read the same as
"[h264 @".
- When nothing verifies, a result missing a stream the original had now ranks
below every result that kept them all.
- deps.toml: exiftool is also looked for in /usr/bin/vendor_perl, where Arch
installs it outside a non-login PATH. New extra_dirs field for cases like it.
- make-fixtures.sh and ci-test.sh add /usr/bin/vendor_perl to PATH when it
exists.
- MKV fixture: a keyframe every 0.5 s instead of 1 s, so zeros straddling a
keyframe still leave 95% of the duration. FILEWINCH_FIXTURE_NO_X264=1 builds
the Fedora/openSUSE MPEG-4 variant on any machine.
- install-deps.sh --test on openSUSE adds the GLib, FreeType, fontconfig and
libpng libraries Qt loads (libgthread-2_0-0 was missing).
- ci-test.sh smoke lists every library the bundled Qt cannot find, all at
once, before starting the app.


Reasons for changes: The third CI run failed the MKV test on Ubuntu 26.04
(ffmpeg 8.0.1) and Fedora (MPEG-4 fixture), could not find exiftool on Arch,
and could not start Qt on openSUSE. Reproduced in an Ubuntu 26.04 chroot with
its own ffmpeg 8.0.1: the full suite now passes there with both fixture
variants, and on ffmpeg 6.1 and current ffmpeg master locally.

## 2026.11.05 - Bug fixes and package names

- Decode check: newer ffmpeg (7.1 and later) tags log lines as
"[in#0/matroska,webm @" and "[out#0/null @". The check now reads both forms,
so demuxer read-ahead errors no longer place damage too early and null-muxer
timestamp warnings no longer count as decode errors.
- Resume test: a carve counts as interrupted when PhotoRec leaves its session
file, even if every file was already found. It retries when a carve
finishes before the stop lands.
- deps.toml: mp3val is apt only (AUR on Arch); jpeginfo and pngcheck are
AUR only on Arch; gpac has no openSUSE main-repo package (Packman). Each
missing one shows where to get it.
- install-deps.sh: prints the package manager's reason for each package that
fails, treats zypper notice codes (100 to 103, 106) as success, and with
FILEWINCH_CI=1 lets zypper resolve conflicts. CI sets it.
- install-deps.sh --test also installs shellcheck, so the shellcheck test
runs in CI. openSUSE no longer installs gawk; its busybox awk is enough.
- Workflow: the openSUSE checkout step accepts zypper exit code 106 (a
repository was skipped).


Reasons for changes: The second CI run failed the MKV and resume tests on
the newer ffmpeg and faster runners, failed deps-check on Fedora, openSUSE
and Arch on package names, and stopped one openSUSE job on zypper's exit
code 106. All 57 tests pass here on both ffmpeg 6.1 and current ffmpeg.

## 2026.11.05 - Build fixes

- build-appimage.sh makes the whole AppDir readable (chmod -R a+rX) before
packing. mktemp and python-appimage left folders at 0700.
- The icon is rendered to PNG with rsvg-convert for .DirIcon, since
appimagetool no longer accepts an SVG there. The SVG stays in
hicolor/scalable.
- python-appimage's own desktop entry and icon are removed before linuxdeploy
runs, and FileWinch's are placed by hand afterwards.
- The build job installs librsvg2-bin.
- Desktop entry Categories is now System;Filesystem; (one main category).
- deps.toml: gpac has no apt package, since Ubuntu 26.04 and Debian dropped
it. The Dependencies tab points to gpac.io/downloads instead. The README's
apt command no longer lists it.


Reasons for changes: The first CI run failed in appimagetool on the
permissions and the SVG .DirIcon, and in deps-check on Ubuntu, which has no
gpac package. The AppImage now builds and passes all 57 acceptance tests
locally, run from inside the AppImage.

## 2026.11.05 - Bug fixes

- The workflow starts every script with bash instead of running it directly.
- build-appimage.sh starts build-untrunc.sh with bash, ci-test.sh starts
make-fixtures.sh with bash, and the test setup does the same.
- README and maintenance.md show the bash form for every script.
- New test that fails if a script call without bash comes back.


Reasons for changes: Files uploaded through GitHub's web UI lose their
executable bit, so the first CI run failed with "Permission denied" on
tests/install-deps.sh.

## 2026.11.04 - Initial release

- Undelete: ddrescue imaging, Sleuth Kit and TestDisk named recovery,
PhotoRec carving, SMART check, resume.
- Optical: data CD/DVD/Blu-ray imaging, audio CD rescue with cdparanoia,
VCD/SVCD with vcdxrip.
- Partition repair: table backup with sfdisk, TestDisk in a terminal,
before/after diff.
- Repair: diagnosis and recipe chains for VOB/MPEG-PS, MKV, MP4/MOV, AVI, TS,
MP3, FLAC, WAV, JPEG, RAW previews, PNG, ZIP/Office, PDF, SQLite. Lossless
FFV1 or lossy H.264/H.265 as a last resort.
- Dependencies tab with per-distro install commands.
- Themes: Light, Dark, Verdigris, Kawaii Light, Kawaii Dark.
- AppImage built and tested by GitHub Actions on five distros, with SLSA
build provenance.


Reasons for changes: First build of the app from the build document.
