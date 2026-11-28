#!/usr/bin/env bash
# Build the Belay .deb package (v0.18.3).
#
#   packaging/linux/build-deb.sh <binary> <version> [output-dir]
#
# Assembles the package tree and runs dpkg-deb. The binary is the
# release belay-linux; the .desktop file comes from this directory
# and the icon from harness/assets. No network, no root: the .deb is
# built as a regular file. Produces belay_<version>_amd64.deb.
set -euo pipefail
# Standard perms: the build machine's umask may be restrictive, and
# directories installed at 0700/0770 would lock normal users out of
# /usr/bin. Everything created here gets 0755 dirs / 0644 files.
umask 022

BINARY="${1:?usage: build-deb.sh <binary> <version> [output-dir]}"
VERSION="${2:?usage: build-deb.sh <binary> <version> [output-dir]}"
OUTDIR="${3:-.}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

[ -f "$BINARY" ] || { printf 'error: binary not found: %s\n' "$BINARY" >&2; exit 1; }
[ -x "$BINARY" ] || { printf 'error: binary is not executable: %s\n' "$BINARY" >&2; exit 1; }
command -v dpkg-deb >/dev/null 2>&1 || { printf 'error: dpkg-deb is required to build the .deb\n' >&2; exit 1; }

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
# dpkg-deb requires the control directory to be 0755-0775;
# mktemp makes 0700.
chmod 0755 "$STAGE"

mkdir -p "$STAGE/DEBIAN" "$STAGE/usr/bin" "$STAGE/usr/share/applications" "$STAGE/usr/share/pixmaps"
chmod 0755 "$STAGE/DEBIAN"
cp "$BINARY" "$STAGE/usr/bin/belay"
chmod 0755 "$STAGE/usr/bin/belay"
cp "$SCRIPT_DIR/belay.desktop" "$STAGE/usr/share/applications/belay.desktop"
chmod 0644 "$STAGE/usr/share/applications/belay.desktop"
cp "$SCRIPT_DIR/../../harness/assets/belay-icon.png" "$STAGE/usr/share/pixmaps/belay.png"
chmod 0644 "$STAGE/usr/share/pixmaps/belay.png"

INSTALLED_SIZE="$(du -s -k "$STAGE/usr" | cut -f1)"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: belay
Version: $VERSION
Section: utils
Priority: optional
Architecture: amd64
Maintainer: Justin Fournier
Installed-Size: $INSTALLED_SIZE
Description: Post-sales AI harness
 Belay is a post-sales AI harness: routing, tools, memory and an
 approval queue around the model, with a local dashboard. It drafts
 the renewal briefs, QBRs and risk flags, and a human approves every
 draft before it leaves the harness.
EOF

# postinst/postrm refresh the desktop database and icon cache when
# the tools exist. Never fail the install over a missing cache tool.
cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database /usr/share/applications || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOF
chmod 0755 "$STAGE/DEBIAN/postinst"
cp "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"
chmod 0755 "$STAGE/DEBIAN/postrm"

DEB="belay_${VERSION}_amd64.deb"
mkdir -p "$OUTDIR"
dpkg-deb --build "$STAGE" "$OUTDIR/$DEB"
printf 'Built %s\n' "$OUTDIR/$DEB"
