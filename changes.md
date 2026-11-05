# Changes

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
