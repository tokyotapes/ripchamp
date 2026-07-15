@echo off
setlocal

echo Stopping the RIPChamp watcher (if running)...
schtasks /end /tn "RIPChampWatcher" >nul 2>&1

echo Stopping the queue server (if running)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8787 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo Starting the RIPChamp watcher (it will start the queue server itself)...
schtasks /run /tn "RIPChampWatcher"

if %ERRORLEVEL% neq 0 (
    echo.
    echo Could not start the watcher task -- is it installed? Run this once first:
    echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode InstallTask
    echo ^(needs an elevated PowerShell^)
    pause
    exit /b 1
)

echo.
echo Done. Give it a couple seconds, then check:
echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode Status
pause
