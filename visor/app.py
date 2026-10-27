"""ContaStock Visor: dashboard de solo lectura para el teléfono y la Mac.

Muestra la copia que el almacén sube a Neon (ver backend/sync_neon.py):
resumen del día, ventas, inventario y deudas pendientes. No puede modificar
nada: no tiene formularios de cambio y cada consulta corre en una transacción
READ ONLY.

Variables de entorno:
    VISOR_DATABASE_URL   URL de Neon (obligatoria)
    VISOR_SECRET_KEY     clave para firmar la sesión (obligatoria en el hosting)
    VISOR_ROLES          roles que pueden entrar, por defecto "admin"
    VISOR_TZ             zona horaria del almacén, por defecto America/Havana
    VISOR_CACHE_MIN      minutos que se reusan los datos sin preguntar a Neon (0)

Local:  python visor/app.py   ->  http://127.0.0.1:5600
"""
import os
import secrets
import sys
import threading
import time
from datetime import date, datetime, timedelta
from functools import wraps

import bcrypt
from flask import (
    Flask, jsonify, redirect, render_template, request, send_from_directory,
    session, url_for,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import datos  # noqa: E402

APP_NAME = "ContaStock Visor"
MAX_INTENTOS = 5
BLOQUEO_MIN = 15


def create_app():
    # carpeta fija: si no, Flask puede tomar las plantillas de la app del almacén
    app = Flask(__name__, root_path=os.path.dirname(os.path.abspath(__file__)))
    app.secret_key = os.getenv("VISOR_SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # en el hosting va por https; en local (http) la cookie no viajaría
        SESSION_COOKIE_SECURE=os.getenv("VISOR_HTTPS", "1") == "1"
                              and bool(os.getenv("VISOR_SECRET_KEY")),
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    roles = {r.strip() for r in os.getenv("VISOR_ROLES", "admin").split(",") if r.strip()}

    # Bloqueo por intentos fallidos en memoria: el visor no escribe en la base,
    # así que no puede usar el contador de la tabla usuarios.
    fallos, fallos_lock = {}, threading.Lock()

    def bloqueado(clave):
        with fallos_lock:
            _, hasta = fallos.get(clave, (0, 0))
            return hasta > time.time()

    def registrar_fallo(clave):
        with fallos_lock:
            n, _ = fallos.get(clave, (0, 0))
            n += 1
            fallos[clave] = (n, time.time() + BLOQUEO_MIN * 60 if n >= MAX_INTENTOS else 0)

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "usuario" not in session:
                if request.path.startswith("/api/"):
                    return jsonify({"error": "sesion"}), 401
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    @app.after_request
    def cabeceras(resp):
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            clave = f"{request.headers.get('X-Forwarded-For', request.remote_addr)}|{username}"
            if bloqueado(clave):
                error = f"Demasiados intentos. Probá de nuevo en {BLOQUEO_MIN} minutos."
            else:
                try:
                    u = datos.buscar_usuario(username)
                except Exception:
                    u, error = None, "No se pudo conectar con los datos. Probá en un momento."
                ok = (u and u.get("activo") and u.get("rol") in roles
                      and bcrypt.checkpw(password.encode(), str(u["password"]).encode()))
                if ok:
                    session.clear()
                    session.permanent = True
                    session["usuario"] = {"username": u["username"], "rol": u["rol"]}
                    return redirect(url_for("panel"))
                if not error:
                    registrar_fallo(clave)
                    error = "Usuario o contraseña incorrectos."
        return render_template("login.html", error=error, app_name=APP_NAME)

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def panel():
        return render_template("panel.html", app_name=APP_NAME,
                               usuario=session["usuario"]["username"])

    def _dia_param():
        try:
            return date.fromisoformat(request.args.get("dia", ""))
        except ValueError:
            return None

    # Por defecto cada apertura lee Neon al momento (VISOR_CACHE_MIN=0). Lo
    # guardado solo se usa si Neon no responde: mejor algo de hace un rato
    # que una pantalla vacía.
    cache_seg = max(int(os.getenv("VISOR_CACHE_MIN", "0")), 0) * 60
    cache, cache_lock = {}, threading.Lock()

    def _api(funcion, *args):
        clave = (funcion.__name__,) + tuple(str(a) for a in args)
        with cache_lock:
            guardado = cache.get(clave)
        if guardado and guardado[0] > time.time():
            return jsonify(guardado[1])
        try:
            datos_nuevos = funcion(*args)
        except Exception as e:
            app.logger.warning("Error leyendo datos: %s", e)
            if guardado:  # mejor algo de hace un rato que nada
                return jsonify(guardado[1])
            return jsonify({"error": "No se pudo leer la copia de Neon. "
                                     "Se reintenta sola."}), 503
        with cache_lock:
            if len(cache) > 200:  # días viejos consultados una vez
                cache.clear()
            cache[clave] = (time.time() + cache_seg, datos_nuevos)
        return jsonify(datos_nuevos)

    @app.route("/api/resumen")
    @login_required
    def api_resumen():
        return _api(datos.resumen, _dia_param())

    @app.route("/api/ventas")
    @login_required
    def api_ventas():
        return _api(datos.ventas_del_dia, _dia_param())

    @app.route("/api/inventario")
    @login_required
    def api_inventario():
        return _api(datos.inventario)

    @app.route("/api/deudas")
    @login_required
    def api_deudas():
        return _api(datos.deudas_pendientes)

    @app.route("/api/deudas/<int:cliente_id>")
    @login_required
    def api_deudas_cliente(cliente_id):
        return _api(datos.deudas_de_cliente, cliente_id)

    # ---------- App instalable (PWA) ----------
    @app.route("/manifest.webmanifest")
    def manifest():
        resp = jsonify({
            "name": APP_NAME, "short_name": "Visor",
            "description": "Ventas, inventario y deudas del almacén",
            "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#0f172a", "theme_color": "#2563eb",
            "icons": [
                {"src": "/static/icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {"src": "/static/icons/maskable-512.png", "sizes": "512x512",
                 "type": "image/png", "purpose": "maskable"},
            ],
        })
        resp.mimetype = "application/manifest+json"
        return resp

    @app.route("/sw.js")
    def sw():
        resp = send_from_directory(app.static_folder, "sw.js",
                                   mimetype="application/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/salud")
    def salud():
        return {"ok": True, "hora": datetime.now(datos.ZONA).isoformat(timespec="seconds")}

    return app


app = create_app()

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))
    os.environ.setdefault("VISOR_HTTPS", "0")
    app = create_app()
    app.run(host=os.getenv("VISOR_HOST", "127.0.0.1"),
            port=int(os.getenv("VISOR_PORT", "5600")), debug=False)
