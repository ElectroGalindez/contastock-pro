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


def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

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
    webview.create_window(**kwargs)
    webview.start(debug=False)


if __name__ == "__main__":
    main()