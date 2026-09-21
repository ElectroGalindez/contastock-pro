# -*- mode: python ; coding: utf-8 -*-
#
# Especificación de PyInstaller para ContaStock Pro.
# Genera la app de escritorio con todas las plantillas, estáticos y
# recursos incorporados. Funciona en macOS (BUNDLE .app) y Windows (EXE).
#
# Uso:
#   pyinstaller packaging/ContaStockPro.spec --clean --noconfirm

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

# SPECPATH apunta a la carpeta "packaging"
PROJECT_ROOT = Path(SPECPATH).parent
APP_VERSION = "1.0.0"

# ---------- Recursos (templates, estáticos, logo) ----------
datas = [
    (str(PROJECT_ROOT / "templates"), "templates"),
    (str(PROJECT_ROOT / "static"), "static"),
    (str(PROJECT_ROOT / "assets" / "logo.png"), "assets"),
]

# ---------- Módulos dinámicos que PyInstaller no detecta ----------
hiddenimports = [
    "bcrypt",
    "openpyxl",
    "reportlab",
    "dotenv",
    "flask",
    "sqlalchemy",
    "sqlalchemy.dialects.sqlite",
    "psycopg2",
    "webview",
]
hiddenimports += collect_submodules("webview")

datas += collect_data_files("webview")

# ---------- Análisis ----------
a = Analysis(
    [str(PROJECT_ROOT / "desktop.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas", "plotly", "streamlit", "numpy"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

# ---------- Ejecutable ----------
APP_NAME = "ContaStockPro"
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ---------- Bundle macOS (.app) ----------
if sys.platform == "darwin":
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        icon=str(PROJECT_ROOT / "packaging" / "icons_mac" / "contastock.icns"),
        version=APP_VERSION,
        bundle_identifier="com.electrogalindez.contastock",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleDisplayName": "ContaStock Pro",
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "NSAppTransportSecurity": {"NSAllowsArbitraryLoads": True},
        },
    )
else:
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name=APP_NAME,
    )