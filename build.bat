@echo off
setlocal

:: Default: --onefile  →  dist\Focus.exe  (single portable file)
:: Pass "fast" to use --onedir instead  →  dist\Focus\Focus.exe  (~3x faster build, reuses cache)
set MODE=release
if /i "%~1"=="fast" set MODE=dev

echo ============================================================
echo  Focus - Build Script  [%MODE% mode]
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ and add it to PATH.
    pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)

:: Generate icon (only when icon file is missing)
if not exist "focus_icon.ico" (
    echo [2/3] Generating icon...
    python icon_gen.py
    if errorlevel 1 echo [WARNING] Icon generation failed, continuing without custom icon.
) else (
    echo [2/3] Icon already exists, skipping generation.
)

:: Run PyInstaller
echo [3/3] Building Focus...

:: release (default): --onefile produces a single dist\Focus.exe
:: fast: --onedir keeps the build/ cache, produces dist\Focus\Focus.exe (~3x quicker)
if "%MODE%"=="release" (
    if exist "dist\Focus.exe" del /q "dist\Focus.exe"
    set BUNDLE_FLAG=--onefile
    set DEST=dist\Focus.exe
) else (
    if exist "dist\Focus" rmdir /s /q "dist\Focus"
    set BUNDLE_FLAG=--onedir
    set DEST=dist\Focus\Focus.exe
)

python -m PyInstaller ^
    --name Focus ^
    %BUNDLE_FLAG% ^
    --windowed ^
    --icon=focus_icon.ico ^
    --add-data "focus_icon.ico;." ^
    --hidden-import win32api ^
    --hidden-import win32con ^
    --hidden-import win32gui ^
    --hidden-import win32process ^
    --hidden-import pywintypes ^
    --hidden-import psutil ^
    --hidden-import keyboard ^
    --hidden-import pystray ^
    --hidden-import "pystray._win32" ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete!
echo  Run:  %DEST%
echo ============================================================
pause
