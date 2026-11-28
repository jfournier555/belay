#!/usr/bin/env bash
# Build the Belay AppImage (v0.18.3).
#
#   packaging/linux/build-appimage.sh <binary> <version> [output-dir]
#
# Assembles AppDir from the release belay-linux, the shared .desktop
# file and the icon, then runs appimagetool (APPIMAGETOOL env or
# appimagetool on PATH). The AppImage is not signed. Produces
# Belay-<version>-x86_64.AppImage.
#
# Written so its steps are functions and main() only runs when the
# script is executed, never when sourced: the tests source it and call
# assemble_appdir directly.
set -euo pipefail
# Standard perms (see build-deb.sh): the AppDir is extracted as the
# user, so restrictive directory modes would lock the app out.
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"

# assemble_appdir: the AppDir layout, without appimagetool, so the
# layout can be tested on a machine without it.
assemble_appdir() {
  local appdir="$1" binary="$2"
  mkdir -p "$appdir/usr/bin"
  cp "$binary" "$appdir/usr/bin/belay"
  chmod 0755 "$appdir/usr/bin/belay"
  cp "$SCRIPT_DIR/belay.desktop" "$appdir/belay.desktop"
  chmod 0644 "$appdir/belay.desktop"
  cp "$SCRIPT_DIR/../../harness/assets/belay-icon.png" "$appdir/belay.png"
  chmod 0644 "$appdir/belay.png"
  cat > "$appdir/AppRun" <<'EOF'
#!/bin/sh
# AppRun: forward to the bundled binary, wherever the AppImage is.
HERE="$(dirname "$0")"
exec "$HERE/usr/bin/belay" "$@"
EOF
  chmod 0755 "$appdir/AppRun"
}

main() {
  local binary="${1:?usage: build-appimage.sh <binary> <version> [output-dir]}"
  local version="${2:?usage: build-appimage.sh <binary> <version> [output-dir]}"
  local outdir="${3:-.}"
  local work appdir built
  [ -f "$binary" ] || { printf 'error: binary not found: %s\n' "$binary" >&2; exit 1; }
  [ -x "$binary" ] || { printf 'error: binary is not executable: %s\n' "$binary" >&2; exit 1; }
  work="$(mktemp -d)"
  # Expand $work now: it is local to main(), so a single-quoted trap
  # would see it unbound when EXIT fires after main() returns.
  trap "rm -rf \"$work\"" EXIT
  appdir="$work/Belay.AppDir"
  assemble_appdir "$appdir" "$binary"
  command -v "$APPIMAGETOOL" >/dev/null 2>&1 || { printf 'error: appimagetool not found (set APPIMAGETOOL or put it on PATH)\n' >&2; exit 1; }
  mkdir -p "$outdir"
  (cd "$work" && ARCH=x86_64 VERSION="$version" "$APPIMAGETOOL" Belay.AppDir)
  built="$(ls "$work"/Belay-*-x86_64.AppImage 2>/dev/null || true)"
  [ -n "$built" ] || { printf 'error: appimagetool produced no AppImage\n' >&2; exit 1; }
  mv "$built" "$outdir/"
  printf 'Built %s\n' "$outdir/$(basename "$built")"
}

if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
  main "$@"
fi
