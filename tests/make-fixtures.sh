#!/usr/bin/env bash
# Build damaged test files and deleted-file images for the acceptance tests.
# No root, no mounting, since CI containers cannot mount loop devices.
# Every source file is generated (ffmpeg lavfi testsrc and sine, Python for
# the odd binary edit), so the repo carries no binary fixtures.
#
# Usage: tests/make-fixtures.sh OUTPUT_DIR

set -euo pipefail
export LC_ALL=C

out=${1:-}
if [ -z "$out" ]; then
    printf 'usage: %s OUTPUT_DIR\n' "$0" >&2
    exit 2
fi

need=(ffmpeg ffprobe python3 mkfs.fat mcopy mdel mmd mke2fs debugfs dd truncate sha256sum exiftool zip sqlite3 awk)
missing=()
for cmd in "${need[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'missing commands: %s\n' "${missing[*]}" >&2
    exit 1
fi

mkdir -p -- "$out"
cd -- "$out" || exit 1
mkdir -p repair undelete src

ff() { ffmpeg -nostdin -hide_banner -v error -y "$@"; }

# Stock Fedora and openSUSE ffmpeg builds leave out libx264. Fall back to
# MPEG-4 Part 2 there, so the fixtures still build and the tests that need
# H.264 itself skip instead of failing.
# Listed into a variable first: grep -q in a pipeline closes it early, and the
# SIGPIPE ffmpeg gets would read as failure under pipefail.
encoders=$(ffmpeg -hide_banner -encoders 2>/dev/null || true)
case $encoders in
    *" libx264 "*) have_x264=1 ;;
    *) have_x264=0 ;;
esac
case $encoders in
    *" libopus "*) have_opus=1 ;;
    *) have_opus=0 ;;
esac
if [ "$have_x264" -eq 1 ]; then
    h264=(-c:v libx264 -preset veryfast)
else
    h264=(-c:v mpeg4 -q:v 3)
fi
if [ "$have_opus" -eq 1 ]; then
    opus=(-c:a libopus)
else
    opus=(-c:a aac)
fi

# Overwrite LEN bytes at OFFSET in FILE with SOURCE (/dev/zero or /dev/urandom).
stomp() {
    dd if="$4" of="$1" bs=1 seek="$2" count="$3" conv=notrunc 2>/dev/null
}

filesize() { wc -c <"$1" | tr -d ' '; }

# ---------------------------------------------------------------- repair

# MP4: a good reference and the same recording with its moov atom cut off.
# Default MP4 muxing puts moov at the end, so cutting there removes the index.
ff -f lavfi -i testsrc=size=640x360:rate=30:duration=20 -f lavfi -i sine=frequency=440:duration=20 \
   "${h264[@]}" -c:a aac -shortest src/ref.mp4
ff -f lavfi -i testsrc=size=640x360:rate=30:duration=20 -f lavfi -i sine=frequency=660:duration=20 \
   "${h264[@]}" -c:a aac -shortest src/take2.mp4
ffprobe -v trace -i src/take2.mp4 >src/take2.trace 2>&1 || true
moov_pos=$(awk '/type:.moov. parent:.root./ { for (i = 1; i <= NF; i++) if ($i == "sz:") { print $(i + 2) - 8; exit } }' src/take2.trace)
cp -- src/ref.mp4 repair/reference.mp4
head -c "$moov_pos" src/take2.mp4 >repair/no_moov.mp4

# MKV with 64 KiB of zeros in the middle.
ff -f lavfi -i testsrc=size=640x360:rate=30:duration=30 -f lavfi -i sine=duration=30 \
   -vf noise=alls=25:allf=t+u "${h264[@]}" -g 30 -b:v 8M "${opus[@]}" repair/zeroed.mkv
size=$(filesize repair/zeroed.mkv)
stomp repair/zeroed.mkv $((size / 2)) 65536 /dev/zero

# VOB: MPEG-2 video and AC-3 audio in a program stream, 1 MiB of noise in the middle.
ff -f lavfi -i testsrc=size=720x480:rate=30000/1001:duration=40 -f lavfi -i sine=duration=40 \
   -c:v mpeg2video -b:v 4M -c:a ac3 -f vob repair/damaged.vob
size=$(filesize repair/damaged.vob)
stomp repair/damaged.vob $((size / 2)) 1048576 /dev/urandom

# AVI without its idx1 index. idx1 is the last chunk, so cut it off.
ff -f lavfi -i testsrc=size=640x360:rate=25:duration=20 -c:v mpeg4 -q:v 4 src/indexed.avi
python3 - src/indexed.avi repair/no_index.avi <<'PY'
import struct, sys
data = open(sys.argv[1], "rb").read()
pos, cut = 12, None
while pos + 8 <= len(data):
    cid, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
    if cid == b"idx1":
        cut = pos
        break
    pos += 8 + size + (size & 1)
assert cut, "no idx1 found"
body = bytearray(data[:cut])
body[4:8] = struct.pack("<I", len(body) - 8)
open(sys.argv[2], "wb").write(body)
PY

# A file no remux can fix: noise over the middle of an MPEG-4 AVI. The
# corrupt frames survive a remux, so only the re-encode decodes clean.
ff -f lavfi -i testsrc=size=640x360:rate=25:duration=12 -c:v mpeg4 -q:v 4 -g 250 repair/needs_reencode.avi
size=$(filesize repair/needs_reencode.avi)
stomp repair/needs_reencode.avi $((size / 2)) 200000 /dev/urandom

# JPEG with an embedded thumbnail, truncated at 60%.
ff -f lavfi -i testsrc=size=1600x1200 -frames:v 1 -q:v 2 src/photo.jpg
ff -f lavfi -i testsrc=size=160x120 -frames:v 1 -q:v 5 src/thumb.jpg
exiftool -q -overwrite_original '-ThumbnailImage<=src/thumb.jpg' src/photo.jpg
size=$(filesize src/photo.jpg)
head -c $((size * 60 / 100)) src/photo.jpg >repair/truncated.jpg

# PNG with one flipped CRC byte (IHDR's CRC starts at byte 29).
ff -f lavfi -i testsrc=size=320x240 -frames:v 1 repair/bad_crc.png
python3 - repair/bad_crc.png <<'PY'
import sys
p = sys.argv[1]
b = bytearray(open(p, "rb").read())
b[29] ^= 0xFF
open(p, "wb").write(b)
PY

# ZIP with its central directory cut off.
mkdir -p src/zipdir
for i in 1 2 3 4 5 6; do
    ff -f lavfi -i testsrc=size=320x240 -frames:v 1 "src/zipdir/image-$i.png"
done
(cd src/zipdir && zip -q -X ../whole.zip ./*.png)
python3 - src/whole.zip repair/no_central_dir.zip <<'PY'
import sys, zipfile
src, dst = sys.argv[1], sys.argv[2]
with zipfile.ZipFile(src) as z:
    names = [i.filename for i in z.infolist()]
data = open(src, "rb").read()
# Keep every local entry, drop the central directory and everything after it.
first_cd = data.find(b"PK\x01\x02")
open(dst, "wb").write(data[:first_cd])
open(dst + ".names", "w").write("\n".join(names) + "\n")
PY

# PDF with a broken cross-reference table.
python3 - repair/broken_xref.pdf <<'PY'
import sys
objs = [
    b"<< /Type /Catalog /Pages 2 0 R >>",
    b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
    b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
    None,
    b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
]
stream = b"BT /F1 24 Tf 72 700 Td (FileWinch test page) Tj ET"
objs[3] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
out = bytearray(b"%PDF-1.4\n")
offsets = []
for n, body in enumerate(objs, 1):
    offsets.append(len(out))
    out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
xref = len(out)
out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
for off in offsets:
    # Every offset deliberately wrong by 7 bytes.
    out += b"%010d 00000 n \n" % (off + 7)
out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref + 3)
open(sys.argv[1], "wb").write(out)
PY

# SQLite with one corrupted page.
rm -f repair/corrupt.db
sqlite3 repair/corrupt.db <<'SQL'
PRAGMA page_size=4096;
CREATE TABLE photos(id INTEGER PRIMARY KEY, name TEXT, note TEXT);
CREATE TABLE trips(id INTEGER PRIMARY KEY, place TEXT);
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000)
  INSERT INTO photos(name, note) SELECT 'img_' || i || '.jpg', printf('%.200c', 'x') FROM n;
WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 50)
  INSERT INTO trips(place) SELECT 'place ' || i FROM n;
SQL
pages=$(( $(filesize repair/corrupt.db) / 4096 ))
stomp repair/corrupt.db $(( (pages / 2) * 4096 + 100 )) 512 /dev/urandom

# A .mov that is really an MP4.
cp -- src/ref.mp4 repair/really_mp4.mov

# ---------------------------------------------------------------- undelete

# Long lowercase names, so FAT keeps them in VFAT long-name entries.
make_set() {
    dir=$1
    mkdir -p -- "$dir"
    for i in 01 02 03 04 05; do
        ff -f lavfi -i "testsrc=size=800x600:rate=1" -ss "$i" -frames:v 1 -q:v 3 "$dir/holiday-photo-$i.jpg"
        ff -f lavfi -i "testsrc2=size=400x300:rate=1" -ss "$i" -frames:v 1 "$dir/screenshot-$i.png"
        python3 - "$dir/report-$i.pdf" "$i" <<'PY'
import sys
path, tag = sys.argv[1], sys.argv[2]
text = ("BT /F1 18 Tf 72 700 Td (Report %s) Tj ET" % tag).encode()
objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(text) + text + b"\nendstream"]
out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
offs = []
for n, body in enumerate(objs, 1):
    offs.append(len(out))
    out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
x = len(out)
out += b"xref\n0 5\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % o for o in offs)
out += b"trailer\n<< /Size 5 /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % x
open(path, "wb").write(out)
PY
        head -c 30000 /dev/urandom >"$dir/notes-file-$i.bin"
    done
}

# FAT32: 20 files, 10 deleted.
make_set src/fat
truncate -s 64M undelete/fat32.img
mkfs.fat -F 32 -n FWTEST undelete/fat32.img >/dev/null
mmd -i undelete/fat32.img ::/camera-roll
for f in src/fat/*; do
    case $f in
        *.jpg) mcopy -i undelete/fat32.img "$f" ::/camera-roll/ ;;
        *) mcopy -i undelete/fat32.img "$f" ::/ ;;
    esac
done
: >undelete/fat32.deleted
for i in 01 02 03; do
    mdel -i undelete/fat32.img "::/camera-roll/holiday-photo-$i.jpg"
    printf '%s  camera-roll/holiday-photo-%s.jpg\n' "$(sha256sum <"src/fat/holiday-photo-$i.jpg" | cut -d' ' -f1)" "$i" >>undelete/fat32.deleted
done
for i in 01 02 03; do
    mdel -i undelete/fat32.img "::/report-$i.pdf"
    printf '%s  report-%s.pdf\n' "$(sha256sum <"src/fat/report-$i.pdf" | cut -d' ' -f1)" "$i" >>undelete/fat32.deleted
done
for i in 01 02; do
    mdel -i undelete/fat32.img "::/screenshot-$i.png"
    printf '%s  screenshot-%s.png\n' "$(sha256sum <"src/fat/screenshot-$i.png" | cut -d' ' -f1)" "$i" >>undelete/fat32.deleted
    mdel -i undelete/fat32.img "::/notes-file-$i.bin"
    printf '%s  notes-file-%s.bin\n' "$(sha256sum <"src/fat/notes-file-$i.bin" | cut -d' ' -f1)" "$i" >>undelete/fat32.deleted
done

# ext4: 20 files built with mke2fs -d, 10 removed with debugfs.
make_set src/ext
truncate -s 64M undelete/ext4.img
mke2fs -q -t ext4 -d src/ext undelete/ext4.img
: >undelete/ext4.deleted
for f in holiday-photo-01.jpg holiday-photo-02.jpg holiday-photo-03.jpg report-01.pdf report-02.pdf \
         report-03.pdf screenshot-01.png screenshot-02.png notes-file-01.bin notes-file-02.bin; do
    debugfs -w -R "rm /$f" undelete/ext4.img >/dev/null 2>&1
    printf '%s  %s\n' "$(sha256sum <"src/ext/$f" | cut -d' ' -f1)" "$f" >>undelete/ext4.deleted
done

# Reformatted: JPEG, PNG, PDF and MP4 on FAT32, then mkfs.fat over it again.
truncate -s 64M undelete/reformatted.img
mkfs.fat -F 32 undelete/reformatted.img >/dev/null
: >undelete/reformatted.expected
for f in src/fat/holiday-photo-04.jpg src/fat/screenshot-04.png src/fat/report-04.pdf src/ref.mp4; do
    mcopy -i undelete/reformatted.img "$f" ::/
    printf '%s  %s\n' "$(sha256sum <"$f" | cut -d' ' -f1)" "${f##*/}" >>undelete/reformatted.expected
done
mkfs.fat -F 32 undelete/reformatted.img >/dev/null

# Resume: a bigger image so a carve takes long enough to interrupt.
truncate -s 900M undelete/resume.img
mkfs.fat -F 32 undelete/resume.img >/dev/null
for f in src/fat/holiday-photo-0[1-3].jpg; do mcopy -i undelete/resume.img "$f" ::/; done
head -c 400000000 /dev/urandom >src/filler.bin
mcopy -i undelete/resume.img src/filler.bin ::/
for f in src/fat/holiday-photo-0[4-5].jpg src/fat/screenshot-*.png; do mcopy -i undelete/resume.img "$f" ::/; done
mdel -i undelete/resume.img ::/filler.bin
rm -f -- src/filler.bin

printf 'Fixtures written to %s\n' "$PWD"
