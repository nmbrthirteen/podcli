@echo off
setlocal
cd /d "%~dp0"

echo Installing podcli - this can take a few minutes (downloads Whisper, Python and Node packages)...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -Install
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo Install failed with code %RC%. Review the messages above.
) else (
    echo Install complete. To open the studio, run:  powershell -ExecutionPolicy Bypass -File setup.ps1 -Ui
)
echo.
pause
exit /b %RC%
