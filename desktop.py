import webview
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app
from backend.app_meta import APP_NAME

app = create_app()

HOST = "127.0.0.1"
PORT = 5555


def start_flask():
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def main():
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    window = webview.create_window(
        title=f"{APP_NAME} - Sistema de Contabilidad e Inventario",
        url=f"http://{HOST}:{PORT}",
        width=1400,
        height=900,
        min_size=(1024, 700),
        resizable=True,
        text_select=True,
    )
    webview.start(debug=False)


if __name__ == "__main__":
    main()