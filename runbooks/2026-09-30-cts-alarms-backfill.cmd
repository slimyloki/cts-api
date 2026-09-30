@echo off
rem Sends the alarm bot's events of the 44-hour gap to digibuild, report first, then it asks. Double-click: asks for administrator rights, then runs 2026-09-30-cts-alarms-backfill.ps1.
fltmc >nul 2>&1
if errorlevel 1 (
    echo Asking for administrator rights...
    if "%~1"=="" (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    ) else (
        powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    )
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp02026-09-30-cts-alarms-backfill.ps1" %*
echo.
pause
