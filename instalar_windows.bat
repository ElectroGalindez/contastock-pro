@echo off
rem ============================================================
rem  ContaStock Pro - Instalacion y ejecucion (Windows)
rem
rem  Hace todo automatico:
rem    1) Verifica que Python 3.10+ este instalado
rem    2) Crea un entorno virtual (.venv)
rem    3) Instala las dependencias
rem    4) Baja los datos desde Neon a la base SQLite local
rem    5) Abre la app de escritorio
rem
rem  Requisito: el archivo .env (con NEON_DATABASE_URL) debe
rem  estar en la carpeta del proyecto. Si te falta, lo podes
rem  crear con el mismo .env que usas en la Mac.
rem ============================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   ContaStock Pro - Instalacion y Ejecucion
echo ==================================================
echo.

rem ---------- 1) Python ----------
where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: No se encontro Python.
    echo Instala Python 3.10 o superior desde https://www.python.org/downloads/windows/
    echo IMPORTANTE: marca la casilla "Add python.exe to PATH" durante la instalacion.
    start https://www.python.org/downloads/windows/
    pause
    exit /b 1
)
for /f "tokens=2 delims= " %%v in ('python --version') do set PYVER=%%v
echo [1/5] Python detectado: %PYVER%

rem ---------- 2) Entorno virtual ----------
echo [2/5] Creando entorno virtual...
if not exist ".venv\Scripts\activate.bat" python -m venv .venv
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: no se pudo activar el entorno virtual.
    pause
    exit /b 1
)

rem ---------- 3) Dependencias ----------
echo [3/5] Instalando dependencias (puede tardar unos minutos)...
python -m pip install --upgrade pip >nul 2>&1
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: fallo la instalacion de dependencias.
    pause
    exit /b 1
)

rem ---------- 4) .env y migracion desde Neon ----------
if not exist ".env" (
    echo.
    echo AVISO: no existe el archivo .env en esta carpeta.
    echo Podes usar el mismo .env que ya tienes en la Mac,
    echo o crear uno ahora escribiendo la URL de Neon:
    echo.
    set /p NEON="NEON_DATABASE_URL: "
    if "%NEON%"=="" (
        echo ERROR: no se ingreso la URL de Neon. No se puede bajar la base.
        pause
        exit /b 1
    )
    (
        echo # Conexion a Neon PostgreSQL ^(solo para bajar los datos^)
        echo NEON_DATABASE_URL=%NEON%
    ) > .env
    echo .env creado.
)

echo [4/5] Bajando los datos de Neon a la base local ^(SQLite^)...
python scripts\migrar_neon_a_local.py
if errorlevel 1 (
    echo.
    echo ERROR durante la migracion.
    echo Revisa que la URL de Neon en .env sea correcta y que la PC tenga internet.
    pause
    exit /b 1
)

rem ---------- 5) Ejecutar ----------
echo.
echo [5/5] Iniciando ContaStock Pro...
echo Cuando termine de cargar, ingresa con:  admin / admin1234
echo.
python desktop.py

pause
endlocal