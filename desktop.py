import webview
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app import create_app

app = create_app()


def start_flask():
    app.run(host="127.0.0.1", port=5555, debug=False, use_reloader=False)


if __name__ == "__main__":
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    window = webview.create_window(
        title="ElectroGalindez - Sistema de Contabilidad",
        url="http://127.0.0.1:5555",
        width=1400,
        height=900,
        min_size=(1024, 700),
        resizable=True,
        text_select=True,
    )
    webview.start(debug=False)
