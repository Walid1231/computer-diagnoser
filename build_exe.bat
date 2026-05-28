@echo off
title Building Computer Diagnoser .exe
echo ============================================
echo    Building Computer Diagnoser .exe
echo ============================================
echo.
echo    This creates a standalone .exe for sharing.
echo    For your own use, just run start_diagnoser.bat
echo.

cd /d "%~dp0"

REM Install PyInstaller if needed
pip install pyinstaller --quiet

REM Build the exe
pyinstaller --onefile --noconsole --name "ComputerDiagnoser" ^
    --add-data "frontend;frontend" ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.loops ^
    --hidden-import uvicorn.loops.auto ^
    --hidden-import uvicorn.protocols ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    backend/app.py

echo.
echo ============================================
if exist "dist\ComputerDiagnoser.exe" (
    echo    SUCCESS! Your .exe is at:
    echo    %~dp0dist\ComputerDiagnoser.exe
) else (
    echo    BUILD FAILED. Check errors above.
)
echo ============================================
pause
