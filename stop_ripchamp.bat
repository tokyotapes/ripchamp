@echo off
setlocal

echo Stopping the RIPChamp watcher (if running)...
schtasks /end /tn "RIPChampWatcher" >nul 2>&1

echo Stopping the queue server (if running)...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr :8787 ^| findstr LISTENING') do (
    taskkill /F /PID %%p >nul 2>&1
)

timeout /t 1 /nobreak >nul

echo.
echo Done. Give it a couple seconds, then check:
echo   powershell -File "%~dp0ripchamp_tools.ps1" -Mode Status
pause