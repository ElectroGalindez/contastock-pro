@echo off
rem ============================================================
rem  ContaStock Pro - Generar instalador de Windows
rem
rem  Requisitos:
rem    1) Python 3.10+ instalado (en PATH)
rem    2) PyInstaller:  pip install pyinstaller
rem    3) Inno Setup 6 instalado (ISCC.exe en PATH o en la ruta de abajo)
rem
rem  Uso:
rem    packaging\build_windows.bat
rem ============================================================
setlocal
cd /d "%~dp0.."

set VERSION=1.0.0
set APP_NAME=ContaStockPro

echo ============================================
echo  ContaStock Pro - Build Windows
echo ============================================

echo [1/4] Instalando dependencias de build...
pip install "setuptools<81" pyinstaller

echo [2/4] Generando .exe con PyInstaller...
pyinstaller packaging\ContaStockPro.spec --clean --noconfirm
if errorlevel 1 goto :error

echo [3/4] Buscando compilador de Inno Setup...
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=ISCC.exe"

echo [4/4] Generando instalador .exe...
"%ISCC%" "packaging\windows\ContaStockPro.iss"
if errorlevel 1 goto :error

echo.
echo Instalador generado en dist\ContaStockPro-setup.exe
echo.
explorer dist
exit /b 0

:error
echo.
echo ERROR: el build fallo. Revise los mensajes anteriores.
exit /b 1