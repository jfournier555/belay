# Belay one-command install (v1.13.0), Windows.
#
#   git clone https://github.com/jfournier555/belay.git
#   cd belay
#   .\bootstrap.ps1
#
# If the execution policy blocks it, run it as:
#   powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
#
# Creates .venv, installs the dependencies, installs Belay editable,
# copies .env.example to .env when .env is missing, prints one line
# about Ollama, then launches the first-run wizard:
#   python -m harness.run setup
# (Demo data or Setup Wizard; Ollama is the default LLM.)
#
# No activation step anywhere: the script calls the venv binaries
# directly. Re-running it is safe: an existing .venv is reused and an
# existing .env is left alone.
#
# NOTE: this script has not been run on Windows yet (the build machine
# is Linux). If it fails, the manual Windows steps in the README are
# the fallback; they do exactly the same things, one command at a time.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Find-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($null -eq $cmd) {
        throw "Python 3.11 or newer is required, but no python was found on PATH. Install it from https://www.python.org/downloads/ (or the Microsoft Store), then re-run .\bootstrap.ps1."
    }
    & $cmd.Source -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        $ver = & $cmd.Source -c "import sys; print('%d.%d' % sys.version_info[:2])"
        throw "Python 3.11 or newer is required (found Python $ver). Install a newer Python, then re-run .\bootstrap.ps1."
    }
    return $cmd.Source
}

function Test-Ollama {
    # One line about Ollama, then hand off to the wizard for the
    # install or pull offer. Never blocks the install either way.
    # The request bypasses any machine-wide proxy: localhost only.
    try {
        $request = [System.Net.WebRequest]::Create("http://localhost:11434/api/tags")
        $request.Proxy = $null
        $request.Timeout = 2000
        $null = $request.GetResponse()
        Write-Output "Ollama found at http://localhost:11434 (local LLM ready)."
    } catch {
        Write-Output "Ollama not found. The setup wizard will offer to install it."
    }
}

Write-Output "== Checking Python..."
$Python = Find-Python
Write-Output "== Python: $Python"

if (-not (Test-Path .venv)) {
    Write-Output "== Creating the virtual environment..."
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "python -m venv .venv failed." }
} else {
    Write-Output ".venv already exists, reusing it."
}

Write-Output "== Installing dependencies..."
& .\.venv\Scripts\pip.exe install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt failed." }
& .\.venv\Scripts\pip.exe install -e .
if ($LASTEXITCODE -ne 0) { throw "pip install -e . failed." }

Write-Output "== Setting up .env..."
if (Test-Path .env) {
    Write-Output ".env already exists, left alone."
} elseif (Test-Path .env.example) {
    Copy-Item .env.example .env
    Write-Output "Created .env from .env.example."
} else {
    throw ".env.example is missing from this checkout; re-clone the repo and try again."
}

Write-Output "== Checking Ollama..."
Test-Ollama

Write-Output "== Launching the first-run wizard..."
if ([Console]::IsInputRedirected) {
    Write-Output "Setup is ready. Start the first-run wizard with:"
    Write-Output "  .\.venv\Scripts\python.exe -m harness.run setup"
    Write-Output "(Demo data or Setup Wizard; Ollama is the default LLM.)"
} else {
    & .\.venv\Scripts\python.exe -m harness.run setup
}
