#!/usr/bin/env bash
# What the CI test job runs against a built AppImage. Also runnable locally.
#
# Usage: tests/ci-test.sh smoke APPIMAGE   launch once headless, catch startup crashes
#        tests/ci-test.sh full APPIMAGE    the acceptance tests, using the AppImage's
#                                          own Python, app code and untrunc, and the
#                                          host's tools for everything else
#
# Containers have no FUSE, so the AppImage is always extracted, never mounted.
# The full run writes mp4-result.txt (pass, fail or skip) for the untrunc fallback.

set -euo pipefail
export LC_ALL=C

mode=${1:-}
appimage=${2:-}
if [ -z "$mode" ] || [ -z "$appimage" ] || [ ! -f "$appimage" ]; then
    printf 'usage: %s smoke|full APPIMAGE\n' "$0" >&2
    exit 2
fi
for cmd in timeout find; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
        printf 'missing command: %s\n' "$cmd" >&2
        exit 1
    fi
done

repo=$(cd -- "$(dirname -- "$0")/.." && pwd)
appimage=$(cd -- "$(dirname -- "$appimage")" && pwd)/$(basename -- "$appimage")
chmod +x "$appimage"
work=$(mktemp -d "${TMPDIR:-/tmp}/filewinch-ci.XXXXXX")
trap 'rm -rf -- "$work"' EXIT
export HOME="$work/home"
mkdir -p "$HOME"
export QT_QPA_PLATFORM=offscreen

case $mode in
    smoke)
        # The window has no quit button headless; it either crashes quickly or runs
        # until timeout kills it, and running is a pass.
        set +e
        timeout 20 "$appimage" --appimage-extract-and-run >"$work/smoke.log" 2>&1
        code=$?
        set -e
        cat -- "$work/smoke.log"
        if [ "$code" -ne 124 ] && [ "$code" -ne 0 ]; then
            printf 'FileWinch exited with %s during startup\n' "$code" >&2
            exit 1
        fi
        if grep -q 'Traceback' "$work/smoke.log"; then
            printf 'FileWinch printed a traceback during startup\n' >&2
            exit 1
        fi
        printf 'Startup OK\n'
        ;;
    full)
        (cd -- "$work" && "$appimage" --appimage-extract >/dev/null)
        appdir="$work/squashfs-root"
        python_bin=""
        for cand in "$appdir"/opt/python3.*/bin/python3.*; do
            case $cand in
                *-config) ;;
                *) if [ -x "$cand" ]; then python_bin=$cand; fi ;;
            esac
        done
        if [ -z "$python_bin" ]; then
            printf 'no Python inside the AppImage\n' >&2
            exit 1
        fi
        "$python_bin" -s -m pip install --quiet --no-warn-script-location pytest
        export APPDIR="$appdir"
        "$repo/tests/make-fixtures.sh" "$work/fixtures"
        export FILEWINCH_FIXTURES="$work/fixtures"
        # Tests import the app installed in the AppImage, not the checkout.
        set +e
        (cd -- "$repo/tests" && "$python_bin" -s -m pytest -p no:cacheprovider -rs -v . 2>&1) \
            | tee "$work/pytest.log"
        code=${PIPESTATUS[0]}
        set -e
        if grep -q 'test_mp4_without_moov_untrunc PASSED' "$work/pytest.log"; then
            printf 'pass\n' >"$repo/mp4-result.txt"
        elif grep -q 'test_mp4_without_moov_untrunc SKIPPED' "$work/pytest.log"; then
            printf 'skip\n' >"$repo/mp4-result.txt"
        else
            printf 'fail\n' >"$repo/mp4-result.txt"
        fi
        exit "$code"
        ;;
    *)
        printf 'unknown mode: %s\n' "$mode" >&2
        exit 2
        ;;
esac
