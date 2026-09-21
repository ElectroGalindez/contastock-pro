#!/bin/bash
# ============================================================
# ContaStock Pro - Generar instalador de macOS (.dmg)
#
# Requisitos: Python 3.10+, pip, Xcode Command Line Tools
#   pip3 install pyinstaller
#
# Uso:
#   bash packaging/build_mac.sh
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION="1.0.0"
APP_NAME="ContaStockPro"
APP_DIR="dist"
DMG_NAME="ContaStockPro-${VERSION}-macos.dmg"

echo "============================================"
echo "  ContaStock Pro - Build macOS"
echo "============================================"

echo "[1/4] Instalando dependencias de build..."
pip3 install "setuptools<81" pyinstaller

echo "[2/4] Generando .app con PyInstaller..."
pyinstaller packaging/ContaStockPro.spec --clean --noconfirm

echo "[3/4] Creando volumen DMG de instalación..."
if [ -f "$APP_DIR/$DMG_NAME" ]; then
    rm -f "$APP_DIR/$DMG_NAME"
fi

hdiutil create \
    -volname "ContaStock Pro" \
    -srcfolder "$APP_DIR/$APP_NAME.app" \
    -ov -format UDZO \
    "$APP_DIR/$DMG_NAME"

echo "[4/4] ¡Listo!"
echo ""
echo "Instalador generado:"
echo "  $APP_DIR/$DMG_NAME"
open "$APP_DIR"
exit 0