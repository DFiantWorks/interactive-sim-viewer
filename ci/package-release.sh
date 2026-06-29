#!/usr/bin/env bash
# package-release.sh -- pack the PyInstaller binary into a per-platform release archive.
# Used by CI on a v* tag; the repo tree stays version-free, so local builds are unaffected.
#
# Version + platform live in the FOLDER/archive name; the binary inside keeps its stable name
# (fpga-isv / fpga-isv.exe) so the Homebrew formula's install line is identical across versions.
#
#   ci/package-release.sh <version> <platform-label> <binary-path> [out-dir]
#       version: semver without the leading 'v' (e.g. 0.1.0)
#       platform-label: e.g. linux-x86_64, macos-arm64, windows-x86_64
#       binary-path: the built binary (dist/fpga-isv or dist/fpga-isv.exe)
#
# Windows labels are zipped (python's stdlib zipfile -- always present); others are tar.gz'd.

set -euo pipefail

VER="${1:?usage: package-release.sh <version> <platform> <binary-path> [out]}"
PLAT="${2:?missing platform label}"
BIN="${3:?missing binary path}"
OUT="${4:-release}"

NAME="fpga-isv-$VER-$PLAT"
STAGE="$OUT/$NAME"
rm -rf "$STAGE"
mkdir -p "$STAGE"

cp "$BIN" "$STAGE/"
[ -f LICENSE ] && cp LICENSE "$STAGE/"
[ -f README.md ] && cp README.md "$STAGE/"
printf '%s\n' "$VER" > "$STAGE/VERSION"

case "$PLAT" in
  windows-*)
    ( cd "$OUT" && python -m zipfile -c "$NAME.zip" "$NAME" )
    echo "packaged $OUT/$NAME.zip"
    ;;
  *)
    tar -C "$OUT" -czf "$OUT/$NAME.tar.gz" "$NAME"
    echo "packaged $OUT/$NAME.tar.gz"
    ;;
esac

ls -l "$STAGE"
