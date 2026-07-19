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
setlocal enabledelayedexpansion

echo Stopping the RIPChamp watcher (if running)...
schtasks /end /tn "RIPChampWatcher" >nul 2>&1

echo Stopping the queue server (if running)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8787 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo Starting the RIPChamp watcher (it will start the queue server itself)...
schtasks /run /tn "RIPChampWatcher" >nul 2>&1

if !ERRORLEVEL! neq 0 (
    echo No scheduled task found -- starting the watcher directly instead...
    powershell -NoProfile -Command "Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','%~dp0ripchamp_tools.ps1','-Mode','Watch'"

    if !ERRORLEVEL! neq 0 (
        echo.
        echo Could not start the watcher. Try running this manually to see the error:
        echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode Watch
        pause
        exit /b 1
    )

    echo.
    echo Started directly -- this won't auto-start at your next login, since it's
    echo not registered as a scheduled task. To have it start automatically, use
    echo the setup page's "Let RIPChamp Start Automatically?" option, or run this
    echo once from an elevated PowerShell:
    echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode InstallTask
)

echo.
echo Done. Give it a couple seconds, then check:
echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode Status
pause
