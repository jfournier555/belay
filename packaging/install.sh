#!/usr/bin/env bash
# Belay one-command install (v0.18.3): downloads the release binary.
#
#   curl -fsSL https://raw.githubusercontent.com/jfournier555/belay/main/packaging/install.sh | bash
#
# Linux: installs belay-linux to ~/.local/bin/belay.
# macOS: installs Belay.app to /Applications and links belay to
# ~/.local/bin. Version: latest by default; BELAY_VERSION=0.18.3 or
# --version 0.18.3 pins it. Re-running is safe: it overwrites the
# binary. After install, run `belay`: the dashboard opens and the
# first-run wizard sets up the LLM and connectors (Ollama is the
# default, no key; the wizard offers to install it).
#
# Written so its steps are functions and main() only runs when the
# script is executed, never when sourced: the tests source it and call
# the functions directly, with fake uname and curl on PATH.
set -euo pipefail

REPO="jfournier555/belay"
INSTALL_DIR="${BELAY_INSTALL_DIR:-$HOME/.local/bin}"

usage() {
  printf 'usage: install.sh [--version <version>]\n' >&2
  printf '  --version pins a release (default: latest).\n' >&2
}

# detect_os: linux or darwin, from uname. Anything else fails.
detect_os() {
  local os
  os="$(uname -s)"
  os="${os,,}"
  case "$os" in
    linux|darwin) printf '%s' "$os" ;;
    *) printf 'error: unsupported OS: %s\n' "$os" >&2; return 1 ;;
  esac
}

# detect_arch: the machine arch, from uname.
detect_arch() {
  uname -m
}

# asset_for: the release asset for an OS and arch. Linux ships one
# x86_64 binary; macOS ships the DMG for either arch.
asset_for() {
  local os="$1" arch="$2"
  case "$os" in
    linux)
      case "$arch" in
        x86_64) printf 'belay-linux' ;;
        *) printf 'error: no Linux binary for arch: %s\n' "$arch" >&2; return 1 ;;
      esac
      ;;
    darwin) printf 'Belay-macos.dmg' ;;
    *) printf 'error: unsupported OS: %s\n' "$os" >&2; return 1 ;;
  esac
}

# download_url: the download URL for a version ("latest" or a number)
# and an asset. Latest uses the releases/latest redirect, so no API
# call and no JSON parsing.
download_url() {
  local version="$1" asset="$2"
  if [ "$version" = "latest" ]; then
    printf 'https://github.com/%s/releases/latest/download/%s' "$REPO" "$asset"
  else
    printf 'https://github.com/%s/releases/download/v%s/%s' "$REPO" "$version" "$asset"
  fi
}

# download_file: fetch a URL to a path, curl first, wget fallback.
download_file() {
  local url="$1" dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O "$dest" "$url"
  else
    printf 'error: curl or wget is required to download Belay.\n' >&2
    return 1
  fi
}

# path_note: say whether INSTALL_DIR is on PATH, and if not, print
# the line that adds it. Never edits a shell rc file.
path_note() {
  case ":$PATH:" in
    *":$INSTALL_DIR:"*) echo "Belay is on your PATH: $INSTALL_DIR" ;;
    *)
      echo "$INSTALL_DIR is not on your PATH. Add it with:"
      echo "  export PATH=\"\$PATH:$INSTALL_DIR\""
      ;;
  esac
}

install_linux() {
  local url="$1"
  local tmp
  mkdir -p "$INSTALL_DIR"
  tmp="$(mktemp)"
  trap 'rm -f "$tmp"' EXIT
  echo "Downloading Belay..."
  download_file "$url" "$tmp"
  [ -s "$tmp" ] || { printf 'error: download is empty: %s\n' "$url" >&2; return 1; }
  # Explicit 0755: chmod +x leaves group/other bits up to the umask.
  chmod 0755 "$tmp"
  mv "$tmp" "$INSTALL_DIR/belay"
  trap - EXIT
  echo "Installed Belay to $INSTALL_DIR/belay"
  path_note
}

install_macos() {
  local url="$1"
  local tmp
  mkdir -p "$INSTALL_DIR"
  tmp="$(mktemp).dmg"
  trap 'rm -f "$tmp"; hdiutil detach /Volumes/Belay -quiet 2>/dev/null || true' EXIT
  echo "Downloading Belay..."
  download_file "$url" "$tmp"
  [ -s "$tmp" ] || { printf 'error: download is empty: %s\n' "$url" >&2; return 1; }
  hdiutil attach -nobrowse -quiet "$tmp"
  [ -d /Applications/Belay.app ] && rm -rf /Applications/Belay.app
  cp -R /Volumes/Belay/Belay.app /Applications/
  hdiutil detach /Volumes/Belay -quiet
  trap - EXIT
  ln -sf /Applications/Belay.app/Contents/MacOS/belay "$INSTALL_DIR/belay"
  echo "Installed Belay.app to /Applications"
  path_note
}

main() {
  local version="latest"
  while [ $# -gt 0 ]; do
    case "$1" in
      --version) version="${2:?--version needs a value}"; shift 2 ;;
      --version=*) version="${1#--version=}"; shift ;;
      -h|--help) usage; return 0 ;;
      *) printf 'error: unknown argument: %s\n' "$1" >&2; usage; return 1 ;;
    esac
  done
  version="${version#v}"
  local os arch asset url
  os="$(detect_os)" || return 1
  arch="$(detect_arch)"
  asset="$(asset_for "$os" "$arch")" || return 1
  url="$(download_url "$version" "$asset")"
  case "$os" in
    linux) install_linux "$url" ;;
    darwin) install_macos "$url" ;;
  esac
  echo "Run 'belay' to open the dashboard. First run opens the setup wizard."
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
