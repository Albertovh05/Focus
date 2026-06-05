#!/usr/bin/env bash
# build_mac.sh — macOS build script for Focus
# Produces:  dist/Focus.app  (dev mode: fast)
#            dist/Focus.app + dist/Focus-<version>.dmg  (release mode, default)
#
# Usage:
#   ./build_mac.sh           — release build (.app + .dmg)
#   ./build_mac.sh fast      — dev build (.app only, faster)
#
# Requirements:
#   - Python 3.11+
#   - pip packages from requirements_mac.txt
#   - Xcode Command Line Tools (for iconutil)

set -euo pipefail

# ── Version ───────────────────────────────────────────────────────────────────
APP_NAME="Focus"
APP_VERSION="1.3.3"
DIST_DIR="dist"
BUILD_DIR="build"
SPEC_DIR="${BUILD_DIR}/pyinstaller_specs_mac"
WORK_DIR="${BUILD_DIR}/pyinstaller_mac"

MODE="${1:-release}"

echo "============================================================"
echo " Focus - macOS Build Script  v${APP_VERSION}  [${MODE} mode]"
echo "============================================================"
echo ""

# ── Check Python ──────────────────────────────────────────────────────────────
if ! python3 --version &>/dev/null; then
    echo "[ERROR] Python 3 not found. Install Python 3.11+ via python.org or Homebrew."
    exit 1
fi

# ── Install dependencies ──────────────────────────────────────────────────────
echo "[1/4] Installing dependencies..."
python3 -m pip install -r requirements_mac.txt --quiet
echo "      Done."
echo ""

# ── Generate icons ────────────────────────────────────────────────────────────
echo "[2/4] Generating icons..."
python3 icon_gen.py
echo ""

# ── Resolve icon path ─────────────────────────────────────────────────────────
if [ -f "focus_icon.icns" ]; then
    ICON_ARG="--icon focus_icon.icns"
    DATA_ICONS="--add-data focus_icon.icns:. --add-data focus_icon.png:."
elif [ -f "focus_icon.png" ]; then
    ICON_ARG="--icon focus_icon.png"
    DATA_ICONS="--add-data focus_icon.png:."
else
    ICON_ARG=""
    DATA_ICONS=""
fi

# ── Shared PyInstaller flags ──────────────────────────────────────────────────
PYI_FLAGS=(
    --name "${APP_NAME}"
    --windowed
    --noconfirm
    --specpath "${SPEC_DIR}"
    --hidden-import psutil
    --hidden-import keyboard
    --hidden-import pystray
    --hidden-import "pystray._darwin"
    --hidden-import PIL
    --hidden-import "PIL.Image"
    --hidden-import AppKit
    --hidden-import Quartz
)

# Add icon flag (split cleanly)
if [ -n "${ICON_ARG}" ]; then
    PYI_FLAGS+=(${ICON_ARG})
fi

# Add data files (split cleanly)
if [ -f "focus_icon.icns" ]; then
    PYI_FLAGS+=(--add-data "focus_icon.icns:.")
fi
if [ -f "focus_icon.png" ]; then
    PYI_FLAGS+=(--add-data "focus_icon.png:.")
fi
if [ -f "focus_icon.ico" ]; then
    PYI_FLAGS+=(--add-data "focus_icon.ico:.")
fi

# ── Dev (fast) mode — onedir, no DMG ─────────────────────────────────────────
if [ "${MODE}" = "fast" ]; then
    echo "[3/4] Building ${APP_NAME}.app (dev / onedir)..."
    rm -rf "${DIST_DIR:?}/${APP_NAME}.app" "${WORK_DIR}_fast"
    python3 -m PyInstaller "${PYI_FLAGS[@]}" \
        --onedir \
        --distpath "${DIST_DIR}" \
        --workpath "${WORK_DIR}_fast" \
        main.py
    echo ""
    echo "============================================================"
    echo " Build complete!"
    echo " Run:  open ${DIST_DIR}/${APP_NAME}.app"
    echo "============================================================"
    exit 0
fi

# ── Release mode — onedir .app bundle + DMG ───────────────────────────────────
echo "[3/4] Building ${APP_NAME}.app (release / onedir)..."
rm -rf "${DIST_DIR:?}/${APP_NAME}.app" "${WORK_DIR}"
mkdir -p "${DIST_DIR}"
python3 -m PyInstaller "${PYI_FLAGS[@]}" \
    --onedir \
    --distpath "${DIST_DIR}" \
    --workpath "${WORK_DIR}" \
    main.py

echo ""
echo "[4/4] Creating ${APP_NAME}-${APP_VERSION}.dmg..."
DMG_PATH="${DIST_DIR}/${APP_NAME}-${APP_VERSION}.dmg"
[ -f "${DMG_PATH}" ] && rm "${DMG_PATH}"

hdiutil create \
    -volname "${APP_NAME}" \
    -srcfolder "${DIST_DIR}/${APP_NAME}.app" \
    -ov \
    -format UDZO \
    "${DMG_PATH}"

echo ""
echo "============================================================"
echo " Build complete!"
echo "   App:  ${DIST_DIR}/${APP_NAME}.app"
echo "   DMG:  ${DMG_PATH}"
echo ""
echo " NOTE: On first launch, macOS may show a security prompt."
echo "       Go to System Settings > Privacy & Security to allow it."
echo ""
echo " For site-blocking and global hotkeys, grant Accessibility"
echo " permissions in System Settings > Privacy & Security > Accessibility."
echo "============================================================"
