#!/usr/bin/env bash
# Build untrunc (anthwlock fork) from upstream HEAD.
#
# Default: link against the host's current libav (shared). FileWinch's
# AppImage build then copies those libraries in with linuxdeploy.
#
# Fallback: FF_VER=3.3.9 (or whichever version) builds untrunc's own static
# ffmpeg, which some files only repair against. CI uses it only when the
# truncated-MP4 test fails on every distro with the default build.
#
# Usage: packaging/build-untrunc.sh [OUTPUT_DIR]
#        FF_VER=3.3.9 packaging/build-untrunc.sh [OUTPUT_DIR]
# Writes OUTPUT_DIR/bin/untrunc and OUTPUT_DIR/untrunc-build.txt (what was built).

set -euo pipefail
export LC_ALL=C

repo_url="https://github.com/anthwlock/untrunc.git"
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:-"$script_dir/../build"}
ff_ver=${FF_VER:-shared}

need=(git make g++ pkg-config)
if [ "$ff_ver" != "shared" ]; then
    need+=(wget nasm)
fi
missing=()
for cmd in "${need[@]}"; do
    command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done
if [ "$ff_ver" = "shared" ] && ! pkg-config --exists libavformat libavcodec libavutil 2>/dev/null; then
    missing+=("libavformat/libavcodec/libavutil development files")
fi
if [ "${#missing[@]}" -gt 0 ]; then
    printf 'missing: %s\n' "${missing[*]}" >&2
    exit 1
fi

mkdir -p -- "$out/bin"
out=$(cd -- "$out" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/untrunc.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

git clone --quiet --depth 1 -- "$repo_url" "$work/untrunc"
cd -- "$work/untrunc" || exit 1
commit=$(git rev-parse HEAD)

jobs=$(getconf _NPROCESSORS_ONLN 2>/dev/null || printf '2')
if [ "$ff_ver" = "shared" ]; then
    make -j"$jobs"
else
    # untrunc's Makefile downloads and builds that ffmpeg itself.
    make -j"$jobs" FF_VER="$ff_ver"
fi

built=""
for cand in untrunc "untrunc_$ff_ver" untrunc-*; do
    if [ -f "$cand" ] && [ -x "$cand" ]; then
        built=$cand
        break
    fi
done
if [ -z "$built" ]; then
    printf 'untrunc build produced no binary\n' >&2
    exit 1
fi
cp -- "$built" "$out/bin/untrunc"

{
    printf 'untrunc commit: %s\n' "$commit"
    printf 'untrunc source: %s\n' "$repo_url"
    if [ "$ff_ver" = "shared" ]; then
        printf 'ffmpeg: host libav, libavformat %s\n' "$(pkg-config --modversion libavformat)"
    else
        printf 'ffmpeg: static %s (FF_VER fallback)\n' "$ff_ver"
    fi
} >"$out/untrunc-build.txt"

cat -- "$out/untrunc-build.txt"
printf 'untrunc is at %s/bin/untrunc\n' "$out"
