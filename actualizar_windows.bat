@echo off
rem ============================================================
rem  ContaStock Pro - Actualizar la PC del ALMACEN
rem
rem  Usar este (y NO instalar_windows.bat) en la PC que ya tiene
rem  datos. No baja nada de Neon: la base buena es la local.
rem
rem    1) Respalda la base local (%APPDATA%\ContaStockPro\respaldos)
rem    2) Baja la version nueva desde GitHub
rem    3) Actualiza las dependencias
rem    4) Conexion a Neon (pide la clave la 1ra vez) y NEON_SYNC=1
rem    5) Abre la app (al abrir, ajusta la base sola si hace falta)
rem
rem  Cerra la app antes de ejecutar esto.
rem ============================================================
setlocal enableextensions
chcp 65001 >nul
cd /d "%~dp0"

echo ==================================================
echo   ContaStock Pro - Actualizar almacen
echo ==================================================
echo.

if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: no esta instalado el entorno .venv en esta carpeta.
    echo Si es una PC nueva, usa instalar_windows.bat.
    pause
    exit /b 1
)
call .venv\Scripts\activate.bat

rem ---------- 1) Respaldo ----------
echo [1/5] Respaldando la base local...
python scripts\respaldar_base.py
if errorlevel 1 (
    echo ERROR: no se pudo respaldar la base. No se actualiza nada.
    pause
    exit /b 1
)

rem ---------- 2) Codigo nuevo ----------
echo.
echo [2/5] Bajando la version nueva...
if exist ".git" (
    git pull --ff-only https://github.com/ElectroGalindez/contastock-pro.git main
    if errorlevel 1 (
        echo.
        echo ERROR: no se pudo bajar la version nueva ^(sin internet, o hay
        echo cambios hechos a mano en esta carpeta^). La base no se toco.
        pause
        exit /b 1
    )
) else (
    echo Esta carpeta no es un repositorio git.
    echo Copia encima los archivos nuevos del proyecto ^(SIN borrar el .env^)
    echo y despues presiona una tecla para seguir.
    pause
)

rem ---------- 3) Dependencias ----------
echo.
echo [3/5] Actualizando dependencias...
pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: fallo la instalacion de dependencias.
    pause
    exit /b 1
)

rem ---------- 4) Conexion a Neon y subida solo en esta PC ----------
echo.
echo [4/5] Configurando la conexion a Neon...
rem La URL de Neon llega cifrada con el repo (.env.cifrado). La clave no
rem viaja por GitHub: se pide una sola vez y queda en .env.clave.
if exist ".env.cifrado" if not exist ".env.clave" (
    echo Falta la clave para leer la conexion a Neon.
    echo Pegala ^(esta en el archivo .env.clave de la Mac^):
    set /p CLAVE="Clave: "
    call python scripts\secretos.py guardar-clave %%CLAVE%%
)
python scripts\secretos.py verificar
if errorlevel 1 (
    echo AVISO: sin conexion a Neon la app funciona igual, pero no sube nada.
)
if not exist ".env" (
    echo # Configuracion propia de esta PC ^(no se sube a GitHub^)> .env
)
findstr /b /c:"NEON_SYNC=" .env >nul || (
    rem la redireccion va adelante: "echo NEON_SYNC=1>> .env" escribiria
    rem "NEON_SYNC=" sin el 1 (cmd toma "1>>" como redireccion)
    >> .env echo.
    >> .env echo # Esta es la PC del almacen: sube los cambios a Neon
    >> .env echo NEON_SYNC=1
    echo NEON_SYNC=1 agregado al .env
)

rem ---------- 5) Ejecutar ----------
echo.
echo [5/5] Iniciando ContaStock Pro...
echo La primera subida a Neon copia la base entera; despues solo los cambios.
echo.
python desktop.py

pause
endlocal
