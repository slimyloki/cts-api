@echo off
rem Read-only: shows this server's internet address, whether api.digibuild.dk
rem answers, and whether the clock is close enough. Double-click it.
fltmc >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp02026-09-29-internet-address.ps1" %*
echo.
pause
