@echo off
title Computer Diagnoser
echo ============================================
echo    Computer Diagnoser - Starting...
echo ============================================
echo.
echo    [DEV MODE] Edit files and restart to see changes:
echo      - Python (.py): Restart this script
echo      - HTML/CSS/JS:  Just refresh the window
echo.
echo    Press Ctrl+C in this window to stop.
echo ============================================
echo.

cd /d "%~dp0backend"
python app.py

echo.
echo Diagnoser closed.
pause
