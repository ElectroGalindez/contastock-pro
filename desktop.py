import webview
import threading
import socket
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from backend.app_meta import APP_NAME, VERSION

app = create_app()

HOST = "127.0.0.1"
DEFAULT_PORT = 5555


def _free_port() -> int:
    """Devuelve un puerto libre (evita 'port in use' si algo ocupa el default)."""
    for port in (DEFAULT_PORT, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((HOST, port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("No hay puerto libre disponible")


PORT = _free_port()


def start_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def _centered_position(width, height):
    """Posiciona la ventana al centro de la pantalla (solo Windows)."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        x = max((sw - width) // 2, 0)
        y = max((sh - height) // 2, 0)
        return x, y
    except Exception:
        return None


# ------------------------------------------------------------------
# Logo en Windows
# ------------------------------------------------------------------
# Abierta desde el código (abrir_contastock.bat), quien corre la app es
# pythonw.exe y Windows mostraba su ícono, el de Python. Se le da a la app su
# propia identidad en la barra de tareas y se le pone el logo a la ventana.
APP_ID_WINDOWS = "ElectroGalindez.ContaStockPro"


def _ruta_icono():
    from backend.config import resource_path
    ruta = resource_path("packaging/icons_win/contastock.ico")
    return str(ruta) if ruta.exists() else None


def _identidad_windows():
    """Antes de crear ventanas: que la barra de tareas no la agrupe con Python
    y use el ícono de la ventana en vez del de pythonw.exe."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID_WINDOWS)
    except Exception as e:
        print(f"No se pudo fijar la identidad de la app: {e}")


def _poner_logo_windows():
    """Pone el logo (grande y chico) a todas las ventanas de este proceso."""
    icono = _ruta_icono()
    if sys.platform != "win32" or not icono:
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        IMAGE_ICON, LR_LOADFROMFILE, WM_SETICON = 1, 0x10, 0x80
        grande = user32.LoadImageW(None, icono, IMAGE_ICON, user32.GetSystemMetrics(11),
                                   user32.GetSystemMetrics(12), LR_LOADFROMFILE)
        chico = user32.LoadImageW(None, icono, IMAGE_ICON, user32.GetSystemMetrics(49),
                                  user32.GetSystemMetrics(50), LR_LOADFROMFILE)
        if not grande:
            return

        pid = os.getpid()
        ventanas = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def juntar(hwnd, _):
            dueno = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(dueno))
            if dueno.value == pid and user32.IsWindowVisible(hwnd):
                ventanas.append(hwnd)
            return True

        user32.EnumWindows(juntar, 0)
        for hwnd in ventanas:
            user32.SendMessageW(hwnd, WM_SETICON, 1, grande)        # barra de tareas / Alt+Tab
            user32.SendMessageW(hwnd, WM_SETICON, 0, chico or grande)  # barra de título
    except Exception as e:
        print(f"No se pudo poner el logo: {e}")


def _con_logo(ventana):
    """Cuando la ventana aparezca, ponerle el logo (solo hace algo en Windows)."""
    if sys.platform == "win32" and ventana is not None:
        ventana.events.shown += _poner_logo_windows
    return ventana


def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # Sube la base a Neon cada cierto tiempo (si NEON_DATABASE_URL esta en .env)
    from backend import sync_neon
    sync_neon.iniciar_automatica()

    width, height = 1440, 900
    xy = _centered_position(width, height)
    kwargs = dict(
        title=f"{APP_NAME} v{VERSION} - Sistema de Contabilidad e Inventario",
        url=f"http://{HOST}:{PORT}",
        width=width,
        height=height,
        min_size=(1100, 720),
        resizable=True,
        text_select=True,
    )
    if xy:
        kwargs["x"], kwargs["y"] = xy
    _identidad_windows()
    _con_logo(webview.create_window(**kwargs))
    # icon: lo usan Linux (GTK/Qt); en Windows lo pone _poner_logo_windows
    webview.start(debug=False, icon=_ruta_icono())


if __name__ == "__main__":
    main()