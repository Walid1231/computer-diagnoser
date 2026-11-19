@echo off
title Computer Diagnoser
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup, then run this file again.
    pause
    exit /b 1
)

rem First run (or after an update): install the dependencies. Later runs skip this.
python -c "import fastapi, uvicorn, psutil, webview" >nul 2>nul
if errorlevel 1 (
    echo Installing dependencies - this only happens once...
    python -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo Dependency installation failed. Check your internet connection and try again.
        pause
        exit /b 1
    )
)

echo ============================================
echo    Computer Diagnoser - Starting...
echo ============================================
echo.
echo    Tip: right-click this file and choose "Run as administrator"
echo    for full disk-health, event-log and cleaner access.
echo.
echo    Press Ctrl+C in this window to stop.
echo ============================================
echo.

cd /d "%~dp0backend"
python app.py

echo.
echo Diagnoser closed.
pause
