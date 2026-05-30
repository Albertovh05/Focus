@echo off
setlocal

:: ── Version ──────────────────────────────────────────────────────────────────
:: Bump this before every release. Feeds into both PyInstaller and FocusSetup.exe.
set APP_VERSION=1.3.1

:: Default: --onefile  →  dist\Focus.exe  (portable)  +  dist\FocusSetup.exe  (installer)
:: Pass "fast" to use --onedir instead  →  dist\Focus\Focus.exe  (~3x faster, no installer)
set MODE=release
if /i "%~1"=="fast" set MODE=dev

echo ============================================================
echo  Focus - Build Script  v%APP_VERSION%  [%MODE% mode]
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

:: ── Shared PyInstaller flags ─────────────────────────────────────────────────
set PYI_FLAGS=--name Focus --windowed --icon=focus_icon.ico --add-data "focus_icon.ico;." ^
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
    --hidden-import PIL.Image

:: ── Dev (fast) mode: single --onedir build, no installer ────────────────────
if "%MODE%"=="dev" (
    echo [3/3] Building Focus ^(dev / onedir^)...
    if exist "dist\Focus" rmdir /s /q "dist\Focus"
    python -m PyInstaller %PYI_FLAGS% --onedir main.py
    if errorlevel 1 (
        echo.
        echo [ERROR] PyInstaller build failed.
        pause
        exit /b 1
    )
    echo.
    echo ============================================================
    echo  Build complete!
    echo  Run:  dist\Focus\Focus.exe
    echo ============================================================
    pause
    exit /b 0
)

:: ── Release mode: portable .exe + installer setup ───────────────────────────

echo [3/5] Building portable Focus.exe ^(--onefile^)...
if exist "dist\Focus.exe" del /q "dist\Focus.exe"
python -m PyInstaller %PYI_FLAGS% --onefile main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Portable build failed.
    pause
    exit /b 1
)

echo.
echo [4/5] Building installer source ^(--onedir^)...
if exist "dist\installer_src" rmdir /s /q "dist\installer_src"
python -m PyInstaller %PYI_FLAGS% --onedir --distpath dist\installer_src main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Installer source build failed.
    pause
    exit /b 1
)

echo.
echo [5/5] Compiling installer...

:: Locate ISCC.exe — hardcode standard paths to avoid %ProgramFiles(x86)% expansion quirks
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do set "ISCC=%%i"
if not exist "%ISCC%" set "ISCC="

if "%ISCC%"=="" (
    echo.
    echo [WARNING] Inno Setup 6 not found - skipping installer build.
    echo           Download from: https://jrsoftware.org/isdl.php
    echo.
    echo ============================================================
    echo  Portable build complete!
    echo  Run:  dist\Focus.exe
    echo  (Re-run after installing Inno Setup to also get FocusSetup.exe)
    echo ============================================================
    pause
    exit /b 0
)

if exist "dist\FocusSetup.exe" del /q "dist\FocusSetup.exe"
"%ISCC%" /DAppVersion=%APP_VERSION% installer.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Installer compilation failed.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete!
echo  Portable:  dist\Focus.exe
echo  Installer: dist\FocusSetup.exe  ^(v%APP_VERSION%^)
echo ============================================================
pause
