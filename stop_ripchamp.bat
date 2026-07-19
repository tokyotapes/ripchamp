@echo off
:: RIPChamp
:: Copyright (C) 2026  NoOrg
::
:: This program is free software: you can redistribute it and/or modify
:: it under the terms of the GNU General Public License as published by
:: the Free Software Foundation, either version 3 of the License, or
:: (at your option) any later version.
::
:: This program is distributed in the hope that it will be useful,
:: but WITHOUT ANY WARRANTY; without even the implied warranty of
:: MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
:: GNU General Public License for more details.
::
:: You should have received a copy of the GNU General Public License
:: along with this program.  If not, see <http://www.gnu.org/licenses/>.
setlocal

set "PORT=8787"
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "try { $p = (Get-Content '%~dp0ripchamp_config.json' -Raw | ConvertFrom-Json).port; if ($p) { $p } else { 8787 } } catch { 8787 }"`) do set "PORT=%%P"

echo Stopping the RIPChamp watcher (if running)...
schtasks /end /tn "RIPChampWatcher" >nul 2>&1

echo Stopping the queue server (if running)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :%PORT% ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo.
echo Done. Give it a couple seconds, then check:
echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode Status
pause