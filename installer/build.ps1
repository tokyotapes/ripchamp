<#
build.ps1

Builds RIPChampInstaller.exe from ripchamp_installer.py via PyInstaller.
Bundles the payload manifest (kept in sync with PAYLOAD_FILES/PAYLOAD_DIRS
in ripchamp_installer.py) so the resulting exe is self-contained -- no
need for the project source tree at install time.

Requires: pip install pyinstaller

Usage:
    powershell -File installer\build.ps1
#>

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $PSCommandPath
$ProjectRoot = Split-Path -Parent $ScriptDir

Set-Location $ScriptDir

$payloadFiles = @(
    "ripchamp.py",
    "ripchamp_picker.py",
    "ripchamp_queue_server.py",
    "ripchamp_tools.ps1",
    "start_ripchamp.bat",
    "stop_ripchamp.bat",
    "favicon.ico",
    "logo.png",
    "logo2.png"
)

$addDataArgs = @()
foreach ($file in $payloadFiles) {
    $addDataArgs += "--add-data"
    $addDataArgs += "$(Join-Path $ProjectRoot $file);."
}
$addDataArgs += "--add-data"
$addDataArgs += "$(Join-Path $ProjectRoot 'static');static"

# `python -m PyInstaller` rather than the bare `pyinstaller` command --
# pip installs the pyinstaller.exe launcher script into a Scripts folder
# that isn't always on PATH, but the module is always importable via -m.
python -m PyInstaller --onefile --noconsole --name RIPChampInstaller @addDataArgs ripchamp_installer.py

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "Done. Installer at: $(Join-Path $ScriptDir 'dist\RIPChampInstaller.exe')"
} else {
    Write-Host "PyInstaller failed (exit code $LASTEXITCODE)." -ForegroundColor Red
}
