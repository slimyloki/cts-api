@echo off
rem Install or update the TAC Vista alarm bot on this CTS server.
rem Double-click it: it asks for administrator rights, then runs install.ps1
rem from this folder. Switches are passed on, for example (typed in a cmd window):
rem   install.cmd -ResetSecrets     install.cmd -Rollback     install.cmd -Download
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
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
echo.
pause
