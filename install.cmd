@echo off
setlocal
cd /d "%~dp0"

echo Installing podcli - downloads the prebuilt binary (runtimes are provisioned on first run)...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo Install failed with code %RC%. Review the messages above.
) else (
    echo Install complete. Restart your terminal, then run:  podcli
)
echo.
pause
exit /b %RC%
