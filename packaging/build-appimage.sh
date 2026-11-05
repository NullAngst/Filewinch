#!/usr/bin/env bash
# Build FileWinch-x86_64.AppImage. Everything the CI build job runs, also
# runnable locally on any x86_64 Linux with the packages below installed.
#
# The AppImage holds Python, PyQt6, FileWinch and untrunc. Nothing else, since
# every other tool comes from the host.
#
# Needs: bash, curl, git, make, g++, pkg-config, file, rsvg-convert (librsvg2-bin
# or librsvg), python3 (to read the GitHub API reply), and libavformat/libavcodec/libavutil development files
# for the default untrunc build.
#
# Environment:
#   FF_VER=3.3.9          build untrunc against its own static ffmpeg instead
#   PYTHON_MINOR=3.12     which python-appimage series to use (default: newest)
#   PYTHON_APPIMAGE_URL   use this python-appimage AppImage instead of looking one up
#   GITHUB_TOKEN          used for the GitHub API lookup when set (CI sets it)
#
# Usage: packaging/build-appimage.sh [OUTPUT_DIR]

set -euo pipefail
export LC_ALL=C

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
repo=$(cd -- "$script_dir/.." && pwd)
out=${1:-"$repo/dist"}

need=(curl git make g++ pkg-config file python3 sed rsvg-convert)
missing=()
for cmd in "${need[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'missing commands: %s\n' "${missing[*]}" >&2
    exit 1
fi
if [ "$(uname -m)" != "x86_64" ]; then
    printf 'FileWinch builds for x86_64 only.\n' >&2
    exit 1
fi

mkdir -p -- "$out"
out=$(cd -- "$out" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/filewinch-build.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

fetch() {
    # fetch URL DEST
    curl --fail --location --silent --show-error --retry 3 -o "$2" -- "$1"
}

run_appimage() {
    # Containers have no FUSE, so AppImage tools always extract and run.
    APPIMAGE_EXTRACT_AND_RUN=1 "$@"
}

version=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$repo/src/filewinch/__init__.py")
printf 'Building FileWinch %s\n' "$version"

# 1. untrunc
bash "$script_dir/build-untrunc.sh" "$work/untrunc"

# 2. A relocatable Python from python-appimage, newest release unless told otherwise.
if [ -z "${PYTHON_APPIMAGE_URL:-}" ]; then
    api="https://api.github.com/repos/niess/python-appimage/releases?per_page=100"
    auth=()
    if [ -n "${GITHUB_TOKEN:-}" ]; then
        auth=(-H "Authorization: Bearer $GITHUB_TOKEN")
    fi
    curl --fail --silent --show-error --retry 3 "${auth[@]}" -o "$work/releases.json" -- "$api"
    PYTHON_APPIMAGE_URL=$(python3 - "$work/releases.json" "${PYTHON_MINOR:-}" <<'PY'
import json, re, sys
releases = json.load(open(sys.argv[1]))
want = sys.argv[2]
best = None
for r in releases:
    m = re.fullmatch(r"python3\.(\d+)", r.get("tag_name", ""))
    if not m or (want and r["tag_name"] != "python" + want):
        continue
    for a in r.get("assets", []):
        name = a["name"]
        # manylinux2014 keeps the glibc floor low; PyQt6's own wheels set the real floor.
        if name.endswith("-manylinux2014_x86_64.AppImage") and "-cp3" in name and "t-manylinux" not in name:
            key = int(m.group(1))
            if best is None or key > best[0]:
                best = (key, a["browser_download_url"])
if not best:
    sys.exit("no python-appimage release found")
print(best[1])
PY
)
fi
printf 'Python AppImage: %s\n' "$PYTHON_APPIMAGE_URL"
fetch "$PYTHON_APPIMAGE_URL" "$work/python.AppImage"
chmod +x "$work/python.AppImage"
(cd -- "$work" && run_appimage ./python.AppImage --appimage-extract >/dev/null)
appdir="$work/FileWinch.AppDir"
mv -- "$work/squashfs-root" "$appdir"

python_bin=""
for cand in "$appdir"/opt/python3.*/bin/python3.*; do
    case $cand in
        *-config) ;;
        *) if [ -x "$cand" ]; then python_bin=$cand; fi ;;
    esac
done
if [ -z "$python_bin" ]; then
    printf 'no Python inside the python-appimage AppDir\n' >&2
    exit 1
fi
python_rel=${python_bin#"$appdir"/}
printf 'Bundled Python: %s\n' "$python_rel"

# pip install PyQt6 and the app into the bundled Python.
"$python_bin" -s -m pip install --no-warn-script-location --upgrade pip
"$python_bin" -s -m pip install --no-warn-script-location PyQt6 "$repo"

# 3. Clear out python-appimage's own desktop entry, icon and AppRun, so
# nothing below picks them up.
find "$appdir" -maxdepth 1 \( -name '*.desktop' -o -name '*.png' -o -name '*.svg' -o -name '.DirIcon' \) \
    -exec rm -f -- {} +
for d in applications icons metainfo; do
    if [ -d "$appdir/usr/share/$d" ]; then
        find "$appdir/usr/share/$d" -name 'python*' -exec rm -f -- {} +
    fi
done
rm -f -- "$appdir/AppRun"

# 4. untrunc into the AppDir. linuxdeploy only bundles its libav libraries;
# the desktop entry and icon are placed by hand below, since linuxdeploy would
# want a usr/bin/filewinch binary and would pick the SVG for .DirIcon.
mkdir -p -- "$appdir/usr/bin" "$appdir/usr/share/filewinch"
cp -- "$work/untrunc/bin/untrunc" "$appdir/usr/bin/untrunc"
cp -- "$work/untrunc/untrunc-build.txt" "$appdir/usr/share/filewinch/untrunc-build.txt"
cp -- "$work/untrunc/untrunc-build.txt" "$out/untrunc-build.txt"
fetch "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage" \
    "$work/linuxdeploy.AppImage"
chmod +x "$work/linuxdeploy.AppImage"
run_appimage "$work/linuxdeploy.AppImage" --appdir "$appdir" --executable "$appdir/usr/bin/untrunc"

# Desktop entry and icon. appimagetool wants a PNG .DirIcon, so the SVG is
# rendered to PNG; the SVG stays in hicolor/scalable for desktops that use it.
icons="$appdir/usr/share/icons/hicolor"
mkdir -p -- "$appdir/usr/share/applications" "$icons/scalable/apps" "$icons/256x256/apps"
cp -- "$script_dir/filewinch.desktop" "$appdir/usr/share/applications/filewinch.desktop"
cp -- "$script_dir/filewinch.desktop" "$appdir/filewinch.desktop"
cp -- "$script_dir/filewinch.svg" "$icons/scalable/apps/filewinch.svg"
rsvg-convert -w 256 -h 256 -o "$icons/256x256/apps/filewinch.png" "$script_dir/filewinch.svg"
cp -- "$icons/256x256/apps/filewinch.png" "$appdir/filewinch.png"
ln -s filewinch.png "$appdir/.DirIcon"

cat >"$appdir/AppRun" <<EOF
#!/usr/bin/env bash
# FileWinch AppImage entry point. GUI only; arguments are ignored.
set -euo pipefail
# The AppImage runtime sets APPDIR. Fall back to this script's folder for an
# extracted AppDir run by hand.
export APPDIR=\${APPDIR:-\$(cd -- "\$(dirname -- "\$0")" && pwd)}
# The bundled Python finds its own standard library from its location.
# Host tools never see these: FileWinch strips them before running anything.
unset PYTHONHOME PYTHONPATH
exec "\$APPDIR/$python_rel" -s -m filewinch
EOF
chmod +x "$appdir/AppRun"

# Licenses beside the bundled program, as GPL asks.
mkdir -p -- "$appdir/usr/share/licenses/filewinch"
cp -- "$repo/LICENSE" "$appdir/usr/share/licenses/filewinch/LICENSE"

# mktemp makes the work folder 0700 and some of python-appimage's folders come
# out the same way. Everyone who runs the AppImage needs to read it.
chmod -R a+rX "$appdir"
chmod a+rx "$appdir/AppRun"

# 5. Pack.
fetch "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage" \
    "$work/appimagetool.AppImage"
chmod +x "$work/appimagetool.AppImage"
ARCH=x86_64 run_appimage "$work/appimagetool.AppImage" --no-appstream "$appdir" "$out/FileWinch-x86_64.AppImage"
(cd -- "$out" && sha256sum FileWinch-x86_64.AppImage >SHA256SUMS)

printf 'Built %s/FileWinch-x86_64.AppImage\n' "$out"
cat -- "$out/SHA256SUMS"
