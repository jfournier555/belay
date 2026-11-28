# Belay one-command install (v0.18.3): downloads belay.exe.
#
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/jfournier555/belay/main/packaging/install.ps1 | iex"
#
# Installs to %LOCALAPPDATA%\Programs\Belay\belay.exe, adds it to the
# user PATH, creates a Start Menu shortcut. Version: latest by default;
# download the script and run it with -Version 0.18.3 to pin a release.
# Re-running is safe: it overwrites the binary. After install, run
# `belay` from a new terminal: the dashboard opens and the first-run
# wizard sets up the LLM and connectors (Ollama is the default, no key;
# the wizard offers to install it).
#
# This replaces bootstrap.ps1 (v1.13.0, from-source install, never run
# on Windows): one Windows install script, the binary one. The
# from-source steps stay in the README as manual steps.
param([string]$Version = "")
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "jfournier555/belay"
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\Belay"

if ($Version) {
    $Version = $Version.TrimStart("v")
    $Url = "https://github.com/$Repo/releases/download/v$Version/belay.exe"
} else {
    $Url = "https://github.com/$Repo/releases/latest/download/belay.exe"
}

Write-Output "== Downloading Belay..."
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
Invoke-WebRequest -Uri $Url -OutFile (Join-Path $InstallDir "belay.exe") -UseBasicParsing

Write-Output "== Adding Belay to PATH..."
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($UserPath -notlike "*$InstallDir*") {
    [Environment]::SetEnvironmentVariable("Path", "$UserPath;$InstallDir", "User")
    $env:Path += ";$InstallDir"
    Write-Output "Added $InstallDir to your user PATH."
} else {
    Write-Output "$InstallDir is already on your PATH."
}

Write-Output "== Creating the Start Menu shortcut..."
$Shell = New-Object -ComObject WScript.Shell
$Programs = Join-Path ([Environment]::GetFolderPath("StartMenu")) "Programs"
$Link = $Shell.CreateShortcut((Join-Path $Programs "Belay.lnk"))
$Link.TargetPath = Join-Path $InstallDir "belay.exe"
$Link.Arguments = "launch"
$Link.Save()

Write-Output "Installed Belay to $InstallDir"
Write-Output "Run 'belay' from a new terminal, or Belay in the Start Menu. First run opens the setup wizard in the dashboard."
