@echo off
setlocal enabledelayedexpansion
rem podcli - CLI wrapper (Windows)
rem Usage: podcli process video.mp4 --top 5 --transcript file.txt

set "SCRIPT_DIR=%~dp0"

rem Use venv python if available, else fall back to PYTHON_PATH in .env, else `python`.
set "PYTHON=python"
if exist "%SCRIPT_DIR%venv\Scripts\python.exe" (
    set "PYTHON=%SCRIPT_DIR%venv\Scripts\python.exe"
) else if exist "%SCRIPT_DIR%.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%SCRIPT_DIR%.env") do (
        if /I "%%A"=="PYTHON_PATH" if not "%%B"=="" set "PYTHON=%%B"
    )
)

rem Strip a leading / or -- from the first arg so /auto, --auto, auto all match.
set "CMD_CLEAN=%~1"
if defined CMD_CLEAN (
    if "!CMD_CLEAN:~0,2!"=="--" set "CMD_CLEAN=!CMD_CLEAN:~2!"
    if "!CMD_CLEAN:~0,1!"=="/"  set "CMD_CLEAN=!CMD_CLEAN:~1!"
)

rem PodStack slash commands run inside Claude Code / Codex, not the terminal.
for %%P in (auto prep-episode process-transcript generate-titles generate-descriptions plan-thumbnails review-content publish-checklist retro-episode plan-episode produce-shorts) do (
    if /I "!CMD_CLEAN!"=="%%P" (
        echo.
        echo   /%%P is a PodStack command - it runs inside Claude Code or Codex, not the terminal.
        echo.
        echo   How to use:
        echo     1. Open Claude Code:  claude
        echo        Or open Codex:     codex --cd "%SCRIPT_DIR%"
        echo     2. Type:              /%%P
        echo.
        exit /b 0
    )
)

"%PYTHON%" -W ignore::UserWarning "%SCRIPT_DIR%backend\cli.py" %*
exit /b %ERRORLEVEL%
