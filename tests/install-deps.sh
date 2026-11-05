#!/usr/bin/env bash
# Install every package in src/filewinch/deps.toml with whichever package
# manager is present. A package that no longer exists fails the run and is
# named, which is how renamed packages get caught in CI.
#
# Usage: tests/install-deps.sh            deps.toml packages only
#        tests/install-deps.sh --test     also what the tests and the AppImage need
#        tests/install-deps.sh --print    print the package list, install nothing
#
# Runs as root (CI containers) or through sudo.

set -euo pipefail
export LC_ALL=C

script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
deps_file="$script_dir/../src/filewinch/deps.toml"
mode=install
extras=0
while [ "$#" -gt 0 ]; do
    case $1 in
        --test) extras=1 ;;
        --print) mode=print ;;
        *) printf 'unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
    shift
done

pm=""
for cand in apt-get dnf zypper pacman; do
    if command -v "$cand" >/dev/null 2>&1; then
        pm=$cand
        break
    fi
done
if [ -z "$pm" ]; then
    printf 'no supported package manager found (apt, dnf, zypper, pacman)\n' >&2
    exit 1
fi
key=$pm
if [ "$pm" = "apt-get" ]; then
    key=apt
fi

as_root=()
if [ "$(id -u)" -ne 0 ]; then
    if ! command -v sudo >/dev/null 2>&1; then
        printf 'run as root or install sudo\n' >&2
        exit 1
    fi
    as_root=(sudo)
fi

pm_install() {
    case $pm in
        apt-get) DEBIAN_FRONTEND=noninteractive "${as_root[@]}" apt-get install -y --no-install-recommends "$@" ;;
        dnf) "${as_root[@]}" dnf install -y "$@" ;;
        zypper) "${as_root[@]}" zypper --non-interactive install --no-recommends "$@" ;;
        pacman) "${as_root[@]}" pacman -S --needed --noconfirm "$@" ;;
    esac
}

pm_refresh() {
    case $pm in
        apt-get) "${as_root[@]}" apt-get update ;;
        dnf) : ;;
        zypper) "${as_root[@]}" zypper --non-interactive refresh ;;
        pacman) "${as_root[@]}" pacman -Sy --noconfirm ;;
    esac
}

if [ "$mode" = install ]; then
    pm_refresh
    # deps.toml is read with Python's tomllib, so Python comes first.
    if ! command -v python3 >/dev/null 2>&1; then
        case $pm in
            pacman) pm_install python ;;
            *) pm_install python3 ;;
        esac
    fi
fi

packages=()
while IFS= read -r line; do
    packages+=("$line")
done < <(python3 - "$deps_file" "$key" <<'PY'
import sys, tomllib
data = tomllib.load(open(sys.argv[1], "rb"))
seen = []
for name, tool in data["tools"].items():
    if tool.get("bundled"):
        continue
    pkg = tool.get("packages", {}).get(sys.argv[2], "")
    if pkg and pkg not in seen:
        seen.append(pkg)
print("\n".join(seen))
PY
)

if [ "$extras" -eq 1 ]; then
    # Fixture builders, test runner, and the libraries PyQt6's Qt needs to start.
    case $key in
        apt) packages+=(mtools dosfstools e2fsprogs python3-pytest ca-certificates
                        libgl1 libegl1 libfontconfig1 libxkbcommon0 libdbus-1-3 libglib2.0-0) ;;
        dnf) packages+=(mtools dosfstools e2fsprogs gawk python3-pytest
                        mesa-libGL mesa-libEGL fontconfig libxkbcommon dbus-libs glib2) ;;
        zypper) packages+=(mtools dosfstools e2fsprogs gawk python3-pytest
                           Mesa-libGL1 Mesa-libEGL1 fontconfig libxkbcommon0 libdbus-1-3 glib2-tools) ;;
        pacman) packages+=(mtools dosfstools e2fsprogs gawk python-pytest
                           mesa fontconfig libxkbcommon dbus glib2) ;;
    esac
fi

if [ "$mode" = print ]; then
    printf '%s\n' "${packages[@]}"
    exit 0
fi

printf 'Installing with %s: %s\n' "$pm" "${packages[*]}"
# One at a time on failure, so the log names the package that is gone.
if ! pm_install "${packages[@]}"; then
    failed=()
    for p in "${packages[@]}"; do
        pm_install "$p" || failed+=("$p")
    done
    if [ "${#failed[@]}" -gt 0 ]; then
        printf 'These packages could not be installed with %s: %s\n' "$pm" "${failed[*]}" >&2
        printf 'Fix their names in src/filewinch/deps.toml.\n' >&2
        exit 1
    fi
fi
printf 'All packages installed.\n'
