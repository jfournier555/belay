#!/usr/bin/env bash
# Belay one-command install (v1.13.0).
#
#   git clone https://github.com/jfournier555/belay.git
#   cd belay
#   bash bootstrap.sh
#
# Creates .venv, installs the dependencies, installs Belay editable,
# copies .env.example to .env when .env is missing, prints one line
# about Ollama, then hands off to the first-run wizard:
#   python -m harness.run setup
# (Demo data or Setup Wizard; Ollama is the default LLM.)
#
# No activation step anywhere: the script calls the venv binaries
# directly, so it works the same from bash, zsh, and fish. Re-running
# it is safe: an existing .venv is reused and an existing .env is
# left alone.
set -euo pipefail

OLLAMA_URL="http://localhost:11434"

step() {
  printf '== %s\n' "$*"
}

# find_python: print the python3 to use, or print one clear line to
# stderr and return 1 (never a traceback).
find_python() {
  local py
  py="$(command -v python3 || true)"
  if [ -z "$py" ]; then
    printf 'error: Python 3.11 or newer is required, but python3 was not found.\n' >&2
    printf 'Install it first (macOS: brew install python@3.12, Linux: your package manager, Windows: python.org or the Microsoft Store), then re-run bash bootstrap.sh.\n' >&2
    return 1
  fi
  if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
    local ver
    ver="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    printf 'error: Python 3.11 or newer is required (found Python %s at %s).\n' "$ver" "$py" >&2
    printf 'Install a newer Python, then re-run bash bootstrap.sh.\n' >&2
    return 1
  fi
  printf '%s' "$py"
}

# check_ollama: one line about Ollama, then hand off to the wizard for
# the install or pull offer. Never blocks the install either way.
# --noproxy on the curl call (and a no-proxy opener in the fallback)
# so a machine-wide proxy never routes a localhost check out to the
# network.
check_ollama() {
  local py="${PY:-python3}"
  local ok=0
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS -m 2 --noproxy '*' "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
      ok=1
    fi
  elif "$py" -c 'import urllib.request; urllib.request.build_opener(urllib.request.ProxyHandler({})).open("http://localhost:11434/api/tags", timeout=2).read()' >/dev/null 2>&1; then
    ok=1
  fi
  if [ "$ok" = 1 ]; then
    echo "Ollama found at $OLLAMA_URL (local LLM ready)."
  else
    echo "Ollama not found. The setup wizard will offer to install it."
  fi
}

ensure_venv() {
  if [ -d .venv ]; then
    echo ".venv already exists, reusing it."
    return 0
  fi
  "$PY" -m venv .venv
}

install_deps() {
  ./.venv/bin/pip install -r requirements.txt
  ./.venv/bin/pip install -e .
}

# ensure_env: copy .env.example to .env only when .env is missing.
# An existing .env is never touched.
ensure_env() {
  if [ -f .env ]; then
    echo ".env already exists, left alone."
    return 0
  fi
  if [ ! -f .env.example ]; then
    printf 'error: .env.example is missing from this checkout; re-clone the repo and try again.\n' >&2
    return 1
  fi
  cp .env.example .env
  echo "Created .env from .env.example."
}

# launch_wizard: hand off to the first-run wizard. Without an
# interactive terminal the wizard cannot ask its question, so print
# the command instead of hanging.
launch_wizard() {
  if [ -t 0 ]; then
    exec ./.venv/bin/python -m harness.run setup
  fi
  echo "Setup is ready. Start the first-run wizard with:"
  echo "  ./.venv/bin/python -m harness.run setup"
  echo "(Demo data or Setup Wizard; Ollama is the default LLM.)"
}

main() {
  cd "$(dirname "$0")"
  step "Checking Python..."
  PY="$(find_python)" || exit 1
  step "Python: $PY"
  step "Creating the virtual environment..."
  ensure_venv
  step "Installing dependencies..."
  install_deps
  step "Setting up .env..."
  ensure_env || exit 1
  step "Checking Ollama..."
  check_ollama
  step "Launching the first-run wizard..."
  launch_wizard
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  main "$@"
fi
