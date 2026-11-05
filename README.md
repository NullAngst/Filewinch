# FileWinch

FileWinch is a Linux GUI that gets deleted files back off a drive, SD card, USB stick, disc or disk image, and repairs damaged files like a broken VOB, MKV or MP4, for anyone who just lost data and doesn't want to learn TestDisk menus or ffmpeg flags first.

It does no recovery or repair of its own. Every real operation is a tool your distro already ships: GNU ddrescue, TestDisk, PhotoRec, The Sleuth Kit, ffmpeg, MKVToolNix and a handful of small format checkers. FileWinch picks the right one, runs it with safe flags, reads what it says and tells you in plain words. The one bundled tool is untrunc, since most distros don't package it.

GUI only, x86_64 Linux only, GPL-3.0-or-later.

## What it does

- **Recover deleted files.** Images the drive first with ddrescue (bad-sector aware, resumable), lists deleted files with their original names and folders through The Sleuth Kit, then carves the rest with PhotoRec. Carved files land in folders by type.
- **Discs.** Data CDs, DVDs and Blu-rays are imaged to an ISO. Home-recorded DVDs and Blu-rays get their VOB, VRO or M2TS files pulled and sent to Repair. Audio CDs are ripped with cdparanoia's error correction, Video CDs with vcdxrip.
- **Repair a file.** Says what is wrong (mislabeled type, missing index, decode errors with time ranges, zeroed regions, truncation), then runs that format's repair recipes in order until one verifies. Lossless fixes always come first. A re-encode only runs if you say so, and it asks with a size estimate for each choice.
- **Fix a partition table.** Backs up the table with sfdisk, opens TestDisk in a terminal for you to drive, then shows exactly what changed.

Formats with repair recipes: VOB/MPEG-PS, MKV/WebM, MP4/MOV/M4V/3GP, AVI, MPEG-TS/M2TS, MP3, FLAC, WAV/AIFF, JPEG, camera RAW (embedded previews), PNG, ZIP and Office files, EPUB, PDF and SQLite.

## Safety rules it enforces

- It never writes to the drive or file you are recovering from. The output folder must be on a different drive, and there is no override.
- A source mounted read-write gets offered an unmount first.
- Repairs never touch the original. Its SHA-256 is taken before and after, and the report shows both.
- Imaging first is the default. A drive with pending or reallocated sectors is always imaged first.
- Partition repair refuses to start without a successful table backup, or while any partition on the drive is mounted.
- Only a short whitelist of tools ever runs as root, and only for raw device reads.

## What it can't do

Be clear on these before you rely on it:

- **SSDs and NVMe with TRIM.** Once the drive discards deleted blocks they read back as zeros. Names may still be listed, carving will find nothing. FileWinch warns you up front.
- **Encrypted volumes.** Unlock with cryptsetup yourself, then point FileWinch at `/dev/mapper/<name>`.
- **Commercial discs.** CSS DVDs and AACS Blu-rays are imaged as-is, not decrypted. Discs you recorded yourself are unencrypted and fully supported.
- **Phones over MTP, RAID rebuilds, cloud storage.** Different problems, different tools.
- **NTFS and exFAT** undelete is tested by hand on a real stick, not in CI, since neither can be populated without mounting.

## Prerequisites

- An x86_64 Linux desktop.
- The required tools below, from your package manager. The app's Dependencies tab shows what's found and builds the exact install command for whatever is missing, so you can also just start it and copy the command from there.
- pkexec (polkit) for root access. sudo works as a fallback if you have an askpass helper like ksshaskpass, or cached sudo credentials.
- A terminal emulator (Konsole, GNOME Terminal, xfce4-terminal, kitty, Alacritty or xterm) for partition repair only.

## Install

1. Install the required tools for your distro.

   I'm on Kubuntu, so in my case:

   ```
   sudo apt install util-linux fdisk pkexec smartmontools gddrescue testdisk sleuthkit file ffmpeg
   ```

   Fedora:

   ```
   sudo dnf install util-linux polkit smartmontools ddrescue testdisk sleuthkit file ffmpeg-free
   ```

   openSUSE:

   ```
   sudo zypper install util-linux polkit smartmontools gnu_ddrescue testdisk sleuthkit file ffmpeg
   ```

   Arch:

   ```
   sudo pacman -S --needed util-linux polkit smartmontools ddrescue testdisk sleuthkit file ffmpeg
   ```

   Using something else? Install the same tools with whichever package manager you have; the Dependencies tab lists them.

2. Install the optional tools for the formats and features you want. Each one switches on a feature; without it that feature is greyed out with the reason, and everything else still works. Everything at once, on apt:

   ```
   sudo apt install ntfs-3g btrfs-progs cdparanoia vcdimager mkvtoolnix mp3val flac jpeginfo libjpeg-turbo-progs libimage-exiftool-perl pngcheck libpng-tools imagemagick zip 7zip libarchive-tools qpdf mupdf-tools libreoffice sqlite3 mediainfo
   ```

   On Fedora, openSUSE or Arch, start FileWinch and use Copy Install Command on the Dependencies tab, since that builds the command for your package manager.

2.5. Fedora and openSUSE only: their stock ffmpeg leaves out H.264, H.265 and AC-3, so a lot of video repair won't work. On Fedora enable RPM Fusion and run `sudo dnf swap ffmpeg-free ffmpeg --allowerasing`. On openSUSE add the Packman repository and run `sudo zypper install --from packman ffmpeg`. The Dependencies tab shows ffmpeg as limited until you do.

2.6. TSDuck (`tsanalyze`, for MPEG-TS continuity checks) isn't in the main repos. Get the .deb or .rpm from github.com/tsduck/tsduck/releases, or `tsduck` from the AUR. It's optional; ffmpeg covers TS repair without it.

3. Download `FileWinch-x86_64.AppImage` and `SHA256SUMS` from the latest release.

4. Check the download, since this tool reads raw drives as root:

   ```
   sha256sum -c SHA256SUMS
   ```

5. Make it executable:

   ```
   chmod +x FileWinch-x86_64.AppImage
   ```

6. Run it:

   ```
   ./FileWinch-x86_64.AppImage
   ```

   If a required tool is missing it opens on the Dependencies tab. Otherwise it opens on Home.

## Use it

### Recover deleted files

1. Plug in the drive and STOP USING IT. Every write to it can overwrite what you want back.
2. Click Recover deleted files.
3. Pick the drive, or Open Disk Image if you already have one.
4. Pick an output folder on a different drive, with free space at least the size of the source, since it images first.
5. Click Start. Each stage gets its own progress bar; Live log shows the tool's own output.
6. When it finishes, Open Results. Named files sit under their original folders, carved files under their type.
7. Tick what you want and Save Selected.

Closed the app or rebooted halfway? Start the same recovery with the same output folder. ddrescue picks up from its mapfile. PhotoRec picks up from its session file, and when it can't (PhotoRec 7.1 often can't, unattended), FileWinch carves again from the start and replaces the half-finished output, which gives the same file set.

### Repair a file

1. Click Repair a file.
2. Drop the damaged files on the drop zone, or Add Files.
3. For an MP4 or MOV that won't open at all, pick a reference file: a good video from the same camera or app, recorded with the same settings. untrunc needs it to rebuild the missing index.
4. Click Repair. You'll see what's wrong, then each recipe tick or cross as it runs.
5. Open Output for the repaired file, Open Report for the full story: what was wrong, which recipe worked, error counts before and after, and the original's hash before and after.

### Fix a partition table

1. Click Fix a partition table and pick the drive. TRIPLE-CHECK IT'S THE RIGHT ONE.
2. Click Back Up Table. Note the backup path and the restore command it shows.
3. Click Open TestDisk and drive TestDisk in the terminal. Nothing is written until you choose Write in its menus.
4. Close TestDisk. FileWinch shows the before and after diff of the table.

If it went wrong, the restore command puts the old table back:

```
sudo sfdisk /dev/sdX < ~/.local/share/filewinch/partition-backups/<model>-<serial>-<date>.sfdisk
```

### Every command shown

Each job has a Show Command button that copies the exact command lines it ran, so you can rerun them in a terminal or attach them to a bug report. The Jobs and logs page keeps every job from the session.

## Settings

Kept in `~/.config/filewinch/settings.ini`:

- Output folder.
- Rename carved photos and videos by capture date (exiftool). Off by default.
- Send every carved video to Repair when a carve finishes. Off by default.
- Lossy re-encode codec: H.265 (default) or H.264 for older players.
- Theme: Light, Dark (default, follows the system's light or dark setting), Verdigris, Kawaii Light, Kawaii Dark. Themes switch live.

Want a sixth theme? Drop a .qss file into `src/filewinch/themes/` with a palette block at the top like the others, and it shows up in Settings.

## Run from source

1. Install Python 3.11 or newer and PyQt6: `pip install PyQt6`, or `sudo apt install python3-pyqt6`, or whichever your distro calls it.
2. From the repo folder: `PYTHONPATH=src python3 -m filewinch`
2.5. For the MP4 untrunc recipe: `bash packaging/build-untrunc.sh` builds it into `build/bin/untrunc`, where a source run looks for it. It needs git, make, g++, pkg-config and the libavformat, libavcodec and libavutil development packages.

## Tests

1. `bash tests/install-deps.sh --test` installs every tool in deps.toml plus what the tests need, with whichever package manager you have.
2. `python3 -m pytest tests`

Scripts are run with `bash script.sh` here and in CI, so they work whether or not the executable bit survived the trip to GitHub.

The tests build their own damaged files and deleted-file images with `bash tests/make-fixtures.sh` (no root, no mounting) and run the real tools against them.

## Building the AppImage

`bash packaging/build-appimage.sh` does everything the CI build job does: builds untrunc, grabs a relocatable Python from python-appimage, installs PyQt6 and FileWinch into it, bundles untrunc's libraries with linuxdeploy and packs it with appimagetool. GitHub Actions runs it on every `v*` tag, tests the result on Debian, Ubuntu, Fedora, openSUSE Tumbleweed and Arch against their current packages, attaches signed SLSA build provenance, and publishes the release. The provenance shows up on the repo's Attestations page.

Now FileWinch is installed, the Dependencies tab is green, and it's ready for the day something goes missing.
