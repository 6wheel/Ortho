@echo off
rem ============================================================
rem This outer part's only job is to guarantee the window can
rem NEVER vanish, even if something inside crashes outright with
rem no error message at all (this is exactly what happened on a
rem real test: the window closed itself with no warning right
rem after a "where py" / "where python" check, before reaching
rem any of the error-handling further down). A plain "pause" at
rem the end of a script only helps if execution reaches that
rem line - it does nothing if the script dies somewhere earlier
rem in a way that wasn't anticipated. Relaunching everything
rem inside "cmd /K" instead keeps the window open NO MATTER WHAT
rem happens inside, which is the only way to make this reliable
rem against failures that weren't predicted in advance.
if "%~1"=="run" goto :run
set "SELF=%~f0"
cmd /K call "%SELF%" run
exit /b

:run
setlocal enabledelayedexpansion
title Orthographic Template Generator
color 0B

rem Always work from the folder this file is actually in, regardless of
rem how it was launched (double-click, shortcut, dragged elsewhere) - a
rem common cause of confusing "file not found" failures in launchers.
cd /d "%~dp0"

echo ============================================================
echo   Orthographic Template Generator
echo ============================================================
echo.
echo This window will set everything up and then open the app in
echo your browser. The first run takes a few minutes; after that
echo it will start in a few seconds.
echo.
echo Please don't close this window while the app is running -
echo closing it will shut the app down.
echo.
echo ============================================================
echo.

rem --- Find a working Python launcher -------------------------------
rem Some PCs have "python", some only have "py" (the official Windows
rem launcher), and some have neither set up correctly yet. Try both
rem before giving up, so this doesn't fail for people who installed
rem Python in a slightly different way than expected.
set PYTHON_CMD=

py --version >nul 2>nul
if !errorlevel! == 0 (
    set PYTHON_CMD=py
)

if "!PYTHON_CMD!"=="" (
    python --version >nul 2>nul
    if !errorlevel! == 0 (
        set PYTHON_CMD=python
    )
)

if "!PYTHON_CMD!"=="" (
    color 0C
    echo ------------------------------------------------------------
    echo   Python isn't installed yet - that's the only thing
    echo   missing before this will work.
    echo ------------------------------------------------------------
    echo.
    echo   1. Go to https://www.python.org/downloads/
    echo   2. Download and run the installer
    echo   3. IMPORTANT: on the first install screen, tick the box
    echo      that says "Add python.exe to PATH" before clicking
    echo      Install
    echo   4. Once that finishes, double-click this file again
    echo.
    echo Opening the download page now...
    start https://www.python.org/downloads/
    echo.
    echo You can close this window once you've installed Python,
    echo then double-click this file again to continue.
    goto end
)

echo Found Python - checking it's a recent enough version...
"!PYTHON_CMD!" "_check_python_version.py" >nul 2>nul
if not !errorlevel! == 0 (
    color 0C
    echo.
    echo ------------------------------------------------------------
    echo   The Python on this PC is older than this app needs
    echo   ^(3.9 or newer required^).
    echo ------------------------------------------------------------
    echo   Please install a newer version from:
    echo   https://www.python.org/downloads/
    echo   ^(tick "Add python.exe to PATH" during install^)
    echo.
    start https://www.python.org/downloads/
    goto end
)

echo Python looks good.
echo.

rem --- Install/update dependencies -----------------------------------
rem A marker file records the last successfully-installed requirements.txt,
rem so every launch after the first is fast instead of re-running pip
rem every time. If anything here is even slightly uncertain, fall back
rem to just installing - slower but never wrong.
set MARKER=.deps_installed
set NEED_INSTALL=1

if exist "%MARKER%" (
    fc /b "%MARKER%" "requirements.txt" >nul 2>nul
    if !errorlevel! == 0 set NEED_INSTALL=0
)

if "!NEED_INSTALL!"=="1" (
    echo Setting up required components - this only happens once
    echo and may take a few minutes, depending on your internet
    echo connection. Please be patient...
    echo.
    "!PYTHON_CMD!" -m pip install --upgrade pip --quiet
    "!PYTHON_CMD!" -m pip install -r requirements.txt
    if not !errorlevel! == 0 (
        color 0C
        echo.
        echo ------------------------------------------------------------
        echo   Something went wrong while setting up. The message
        echo   above this box has more detail.
        echo ------------------------------------------------------------
        echo.
        echo   A common fix: just run this file again - sometimes a
        echo   slow download just needs a retry.
        echo.
        echo   If it keeps happening, send a screenshot of this
        echo   whole window so it can be looked into.
        echo.
        goto end
    )
    copy /y requirements.txt "%MARKER%" >nul
    echo.
    echo Setup complete.
    echo.
) else (
    echo Already set up - skipping straight to launch.
    echo.
)

rem --- Launch the app ---------------------------------------------
echo Starting the app...
echo Your browser should open automatically in a few seconds.
echo.
echo ------------------------------------------------------------
echo   Leave this window open while you use the app.
echo   Close this window when you're done to shut it down.
echo ------------------------------------------------------------
echo.

"!PYTHON_CMD!" app.py

echo.
echo ------------------------------------------------------------
echo   The app has stopped.
echo ------------------------------------------------------------

:end
echo.
echo You can close this window now, or just leave it - it won't
echo do anything further until you run this file again.
endlocal
