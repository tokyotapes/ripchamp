<#
RIPChamp
Copyright (C) 2026  NoOrg

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
#>

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
    "ripchamp_secrets.py",
    "ripchamp_tools.ps1",
    "start_ripchamp.bat",
    "stop_ripchamp.bat",
    "COPYING"
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
