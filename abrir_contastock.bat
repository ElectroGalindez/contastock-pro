@echo off
rem ============================================================
rem  ContaStock Pro - Abrir la app (version actualizada)
rem
rem  Abre la app desde el codigo, que es lo que actualiza
rem  actualizar_windows.bat. Usar este (o el acceso directo del
rem  escritorio) en vez del .exe viejo de dist\, que no se
rem  actualiza y no sube a Neon.
rem ============================================================
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
    echo No esta instalado el entorno .venv. Ejecuta instalar_windows.bat.
    pause
    exit /b 1
)
rem pythonw: abre la ventana de la app sin dejar una consola negra abierta
start "" ".venv\Scripts\pythonw.exe" desktop.py
