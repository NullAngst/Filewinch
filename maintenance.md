# Maintenance

This is the checklist for keeping FileWinch working as distros update the
tools it calls. Run it every couple of months. It takes about an hour if
nothing broke.

## What you need

- A browser and the GitHub web UI
- A Linux box to test the AppImage on (I use my Kubuntu machine)
- A spare USB stick, 8 GB or bigger, that you don't mind wiping
- One other distro to test on, a VM is fine
- A burned DVD or CD if you have one lying around

## Steps

1. Check security advisories first, since FileWinch feeds broken files
straight into these parsers on purpose. Search "CVE" plus each of: ffmpeg,
mkvtoolnix, sleuthkit, testdisk, ddrescue, qpdf, libpng, libjpeg-turbo,
exiftool. If a fix landed in a new version, add or raise that tool's minimum
in src/filewinch/deps.toml so the Dependencies tab flags older installs.
2. Check untrunc upstream at github.com/anthwlock/untrunc. Still maintained?
Still the best fork? Then check the last release's notes for which untrunc
build shipped, default or the FF_VER fallback.
3. Go to Actions and make sure the scheduled workflow is still enabled, since
GitHub switches off schedules after 60 days with no repo activity. Re-enable
it if it's off.
4. Pick the build workflow and hit Run workflow on main. This tests against
the current release of all five distros.
5. Wait for it to finish. Green means every package in deps.toml still
installs and every acceptance test passed on every distro.
5.5. If deps-check is red, a package was renamed or dropped. The job log
names it. Fix its line in deps.toml, you'll have to check that distro's
package search for the new name.
5.6. If test is red on one distro only, that distro shipped a tool whose
output changed. Fix the matching parser in src/filewinch/backends/ or
src/filewinch/repair/recipes/, and keep it working on the older format too,
since other distros still have it.
6. Check the run log for GitHub deprecation warnings (Node version, action
versions, a container image that changed name). Fix those now, not when they
break.
7. Download the AppImage from the workflow artifacts.
8. Undelete test on the USB stick: copy a mix of photos, an MKV and a PDF to
it, delete them, then recover them with FileWinch and compare hashes with
sha256sum.
9. Partition test on the same stick: back up its table with FileWinch, wipe
the table with sudo sfdisk --delete /dev/sdX, then get it back with TestDisk
from the Partitions page.

TRIPLE-CHECK THE DEVICE NAME IN STEP 9. /dev/sdX is the USB stick, not your
file server's disks. Get it wrong and you're the one needing FileWinch.

10. Optical test if you have a disc: image it, and for a DVD you recorded,
send its VOBs through Repair.
11. Repair test: run tests/make-fixtures.sh and push the outputs through the
Repair page by hand. Watch the truncated MP4 case, that's the untrunc build.
12. Start the AppImage on the second distro. It should open, list drives, and
the Dependencies tab should show the right install command for that distro.
13. Add an entry to the top of changes.md in its usual format.
14. Commit, then tag with the new version (v2026.11.0 style) and push the
tag. CI builds, tests, attests and publishes the release.
15. Open the repo's Attestations page and confirm the new AppImage is listed.

IF THE SAFETY TESTS FAIL, DO NOT RELEASE. Those check that FileWinch refuses
to write to the source drive and refuses partition repair without a backup. A
broken one means it could destroy the data it's meant to save.

Now deps.toml matches every distro's current packages, the tests pass against
today's tools, and the new release is up, attested, and logged in changes.md.
Tinker further as you see fit.
