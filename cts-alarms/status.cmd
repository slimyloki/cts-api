@echo off
rem Is the alarm bot working? Read-only check; ends with a VERDICT. Double-click it.
rem It asks for administrator rights only so it can read secrets.json's permissions.
fltmc >nul 2>&1
if errorlevel 1 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0status.ps1" %*
echo.
pause
