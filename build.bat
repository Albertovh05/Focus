@echo off
setlocal
pushd "%~dp0"

:: --- Version ---------------------------------------------------------------
:: Bump this before every release. Feeds into both PyInstaller and FocusSetup.exe.
set "APP_NAME=Focus"
set "APP_VERSION=1.3.1"
set "DIST_DIR=dist"
set "BUILD_DIR=build"
set "SPEC_DIR=%BUILD_DIR%\pyinstaller_specs"
set "PORTABLE_WORK=%BUILD_DIR%\pyinstaller_portable"
set "INSTALLER_WORK=%BUILD_DIR%\pyinstaller_installer"
set "INSTALLER_STAGE=%BUILD_DIR%\installer_src"

:: Default: --onefile -> dist\Focus.exe (portable) + dist\FocusSetup.exe (installer)
:: Pass "fast" to use --onedir instead -> dist\Focus\Focus.exe (~3x faster, no installer)
set "MODE=release"
set "NO_PAUSE=0"
if /i "%~1"=="fast" set MODE=dev
if /i "%~1"=="nopause" set NO_PAUSE=1
if /i "%~2"=="nopause" set NO_PAUSE=1

echo ============================================================
echo  Focus - Build Script  v%APP_VERSION%  [%MODE% mode]
echo ============================================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ and add it to PATH.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

:: Install dependencies
echo [1/3] Installing dependencies...
python -m pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] pip install failed.
    if "%NO_PAUSE%"=="0" pause
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

:: --- Shared PyInstaller flags ---------------------------------------------
set "ICON_FILE=%CD%\focus_icon.ico"
set PYI_FLAGS=--name %APP_NAME% --windowed --icon "%ICON_FILE%" --add-data "%ICON_FILE%;."
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import win32api"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import win32con"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import win32gui"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import win32process"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import pywintypes"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import psutil"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import keyboard"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import pystray"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import pystray._win32"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import PIL"
set "PYI_FLAGS=%PYI_FLAGS% --hidden-import PIL.Image"

:: --- Dev (fast) mode: single --onedir build, no installer ------------------
if "%MODE%"=="dev" (
    echo [3/3] Building %APP_NAME% ^(dev / onedir^)...
    if exist "%DIST_DIR%\%APP_NAME%" rmdir /s /q "%DIST_DIR%\%APP_NAME%"
    if exist "%PORTABLE_WORK%" rmdir /s /q "%PORTABLE_WORK%"
    python -m PyInstaller %PYI_FLAGS% --noconfirm --onedir --distpath "%DIST_DIR%" --workpath "%PORTABLE_WORK%" --specpath "%SPEC_DIR%" main.py
    if errorlevel 1 (
        echo.
        echo [ERROR] PyInstaller build failed.
        if "%NO_PAUSE%"=="0" pause
        exit /b 1
    )
    echo.
    echo ============================================================
    echo  Build complete!
    echo  Run:  %DIST_DIR%\%APP_NAME%\%APP_NAME%.exe
    echo ============================================================
    if "%NO_PAUSE%"=="0" pause
    exit /b 0
)

:: --- Release mode: portable .exe + installer setup -------------------------

echo [3/5] Building portable %APP_NAME%.exe ^(--onefile^)...
if not exist "%DIST_DIR%" mkdir "%DIST_DIR%"
if exist "%DIST_DIR%\%APP_NAME%.exe" del /q "%DIST_DIR%\%APP_NAME%.exe"
if exist "%PORTABLE_WORK%" rmdir /s /q "%PORTABLE_WORK%"
python -m PyInstaller %PYI_FLAGS% --noconfirm --onefile --distpath "%DIST_DIR%" --workpath "%PORTABLE_WORK%" --specpath "%SPEC_DIR%" main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Portable build failed.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

echo.
echo [4/5] Building installer source ^(--onedir^)...
if exist "%INSTALLER_STAGE%" rmdir /s /q "%INSTALLER_STAGE%"
if exist "%INSTALLER_WORK%" rmdir /s /q "%INSTALLER_WORK%"
python -m PyInstaller %PYI_FLAGS% --noconfirm --onedir --distpath "%INSTALLER_STAGE%" --workpath "%INSTALLER_WORK%" --specpath "%SPEC_DIR%" main.py
if errorlevel 1 (
    echo.
    echo [ERROR] Installer source build failed.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

echo.
echo [5/5] Compiling installer...

:: Locate ISCC.exe. Hardcode standard paths to avoid %ProgramFiles(x86)% expansion quirks.
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" for /f "delims=" %%i in ('where ISCC.exe 2^>nul') do set "ISCC=%%i"
if not exist "%ISCC%" set "ISCC="

if "%ISCC%"=="" (
    echo.
    echo [ERROR] Inno Setup 6 not found - cannot build %APP_NAME%Setup.exe.
    echo         Download from: https://jrsoftware.org/isdl.php
    echo.
    echo ============================================================
    echo  Portable build complete, installer build failed.
    echo  Portable: %DIST_DIR%\%APP_NAME%.exe
    echo ============================================================
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

if exist "%DIST_DIR%\%APP_NAME%Setup.exe" del /q "%DIST_DIR%\%APP_NAME%Setup.exe"
"%ISCC%" "/DAppVersion=%APP_VERSION%" "/DInstallerSource=%INSTALLER_STAGE%\%APP_NAME%" installer.iss
if errorlevel 1 (
    echo.
    echo [ERROR] Installer compilation failed.
    if "%NO_PAUSE%"=="0" pause
    exit /b 1
)

echo.
echo ============================================================
echo  Build complete!
echo  Portable:  %DIST_DIR%\%APP_NAME%.exe
echo  Installer: %DIST_DIR%\%APP_NAME%Setup.exe  ^(v%APP_VERSION%^)
echo ============================================================
if "%NO_PAUSE%"=="0" pause
