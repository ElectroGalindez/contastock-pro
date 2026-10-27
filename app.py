import os
import sys
import json
import io
from datetime import date
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, send_file, jsonify
)

sys.path.insert(0, os.path.dirname(__file__))

from backend import (
    usuarios, productos, clientes, ventas,
    deudas, categorias, logs
)
from backend.db import (
    get_connection, extract_month, extract_year, month_key, dm_key
)
from backend.config import get_secret_key
from sqlalchemy import text


def _productos_venta(valor):
    """Normaliza productos_vendidos a una lista de dicts serializables.

    El driver puede devolverla como texto JSON crudo o ya parseada, y cada
    linea varia en cantidad/subtotal, asi que se completa lo que falte.
    """
    if not valor:
        return []
    if isinstance(valor, (str, bytes)):
        try:
            valor = json.loads(valor)
        except (ValueError, TypeError):
            return []
    if not isinstance(valor, list):
        return []

    salida = []
    for p in valor:
        if not isinstance(p, dict):
            continue
        try:
            cantidad = float(p.get("cantidad") or 0)
            precio = float(p.get("precio_unitario") or 0)
        except (TypeError, ValueError):
            continue
        try:
            subtotal = float(p.get("subtotal"))
        except (TypeError, ValueError):
            subtotal = cantidad * precio
        salida.append({
            "nombre": str(p.get("nombre") or ""),
            "cantidad": cantidad,
            "precio": precio,
            "subtotal": subtotal,
        })
    return salida


def create_app():
    app = Flask(__name__)
    app.secret_key = get_secret_key()
    app.config["SESSION_TYPE"] = "filesystem"
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

    @app.after_request
    def no_cache_datos(resp):
        """Que el navegador no sirva datos viejos de clientes al volver atras.

        /ventas y /clientes llevan una copia de los clientes embebida en el
        HTML; sin esto, el boton "atras" del navegador puede mostrar nombres que
        ya fueron editados.
        """
        if request.path in ("/ventas", "/clientes", "/ventas/clientes",
                            "/clientes/buscar", "/deudas", "/deudas/buscar",
                            "/ventas_dia"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
        return resp

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "usuario" not in session:
                return redirect(url_for("login"))
            return f(*args, **kwargs)
        return decorated

    def admin_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if "usuario" not in session:
                return redirect(url_for("login"))
            if session["usuario"].get("rol") != "admin":
                flash("Solo administradores pueden acceder.", "danger")
                return redirect(url_for("dashboard"))
            return f(*args, **kwargs)
        return decorated

    # Modo solo lectura: la PC que mira la copia de Neon (el jefe desde la
    # casa). Lo que se guardara ahi se perderia en la proxima subida del
    # almacen, asi que se bloquea cualquier cambio.
    from backend import sync_neon
    from backend.db import DATABASE_URL
    solo_lectura = (
        os.getenv("CONTASTOCK_SOLO_LECTURA", "").strip().lower() in ("1", "true", "si")
        or "neon.tech" in DATABASE_URL
    )
    app.config["SOLO_LECTURA"] = solo_lectura

    @app.after_request
    def subir_cambios_a_neon(resp):
        # Guardó algo: que la PC del almacén lo suba a Neon enseguida (en las
        # demás PCs no hace nada: la subida solo corre con NEON_SYNC=1)
        if request.method in ("POST", "PUT", "DELETE", "PATCH") and resp.status_code < 400:
            sync_neon.avisar_cambio()
        return resp

    @app.before_request
    def bloquear_cambios_en_solo_lectura():
        if not app.config["SOLO_LECTURA"] or request.method in ("GET", "HEAD", "OPTIONS"):
            return None
        if request.endpoint == "login":
            return None
        mensaje = ("Modo consulta: esta copia es solo para ver. "
                   "Los cambios se hacen en la app del almacen.")
        if request.is_json or request.headers.get("X-Requested-With") == "fetch":
            return jsonify({"ok": False, "error": mensaje}), 403
        flash(mensaje, "warning")
        return redirect(request.referrer or url_for("dashboard"))

    @app.context_processor
    def inject_user():
        from backend.app_meta import APP_NAME, VERSION
        return {
            "current_user": session.get("usuario"),
            "app_name": APP_NAME,
            "app_version": VERSION,
            "fecha_hoy": date.today().strftime("%d/%m/%Y"),
            "solo_lectura": app.config["SOLO_LECTURA"],
            "sync_neon_activa": sync_neon.configurada(),
        }

    @app.route("/")
    def index():
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            user = usuarios.autenticar_usuario(username, password)
            if isinstance(user, dict) and user.get("bloqueado"):
                flash(f"Usuario bloqueado hasta: {user['bloqueado_hasta']}", "danger")
                return render_template("login.html")
            if user:
                session["usuario"] = user
                try:
                    usuarios.registrar_log(username, "login", "Inicio de sesion")
                except Exception:
                    pass
                return redirect(url_for("dashboard"))
            flash("Usuario o contrasena incorrectos", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        try:
            today = date.today().isoformat()
            mes_actual = date.today().month
            anio_actual = date.today().year

            with get_connection() as conn:
                total_hoy = conn.execute(text(f"SELECT COALESCE(SUM(total), 0) FROM ventas WHERE DATE(fecha) = :hoy"), {"hoy": today}).scalar()
                ventas_hoy_count = conn.execute(text(f"SELECT COUNT(*) FROM ventas WHERE DATE(fecha) = :hoy"), {"hoy": today}).scalar()
                total_mes = conn.execute(text(f"SELECT COALESCE(SUM(total), 0) FROM ventas WHERE {extract_month('fecha')} = :m AND {extract_year('fecha')} = :y"), {"m": mes_actual, "y": anio_actual}).scalar()
                total_clientes = conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar()
                total_productos = conn.execute(text("SELECT COUNT(*) FROM productos")).scalar()
                stock_bajo = conn.execute(text("SELECT COUNT(*) FROM productos WHERE cantidad <= 5")).scalar()
                total_deuda = conn.execute(text("SELECT COALESCE(SUM(monto_total), 0) FROM deudas WHERE estado = 'pendiente'")).scalar()
                cli_con_deuda = conn.execute(text("SELECT COUNT(DISTINCT cliente_id) FROM deudas WHERE estado = 'pendiente'")).scalar()

                dias_rows = conn.execute(text(f"SELECT {dm_key('fecha')} AS dia, SUM(total) AS total FROM ventas WHERE fecha >= :desde GROUP BY dia ORDER BY dia"), {"desde": date.today()}).fetchall()
                ventas_por_dia = {str(r[0]): float(r[1]) for r in dias_rows}

                mes_rows = conn.execute(text(f"SELECT {month_key('fecha')} AS mes, SUM(total) AS total FROM ventas GROUP BY mes ORDER BY mes")).fetchall()
                ventas_por_mes = {str(r[0]): float(r[1]) for r in mes_rows}

        except Exception as e:
            flash(f"Error al cargar datos: {e}", "danger")
            total_hoy = ventas_hoy_count = total_mes = total_clientes = total_productos = stock_bajo = total_deuda = cli_con_deuda = 0
            ventas_por_dia = ventas_por_mes = {}

        return render_template("dashboard.html",
            total_hoy=total_hoy,
            total_mes=total_mes,
            total_deuda=total_deuda,
            total_productos=total_productos,
            stock_bajo=stock_bajo,
            total_clientes=total_clientes,
            cli_con_deuda=cli_con_deuda,
            ventas_hoy_count=ventas_hoy_count,
            ventas_por_dia=json.dumps(ventas_por_dia),
            ventas_por_mes=json.dumps(ventas_por_mes),
        )

    @app.route("/inventario", methods=["GET", "POST"])
    @login_required
    def inventario():
        prods = productos.list_products() or []
        cats = categorias.list_categories() or []
        cat_map = {c["id"]: c["nombre"] for c in cats}

        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "crear":
                    nombre = request.form.get("nombre", "").strip()
                    precio = float(request.form.get("precio", 0))
                    cantidad = int(request.form.get("cantidad", 0))
                    cat_id = request.form.get("categoria_id")
                    productos.guardar_producto(nombre, precio, cantidad, cat_id, session["usuario"]["username"])
                    flash(f"Producto '{nombre}' creado.", "success")
                elif action == "editar":
                    pid = request.form.get("producto_id")
                    nombre = request.form.get("nombre", "").strip()
                    precio = float(request.form.get("precio", 0))
                    cantidad = int(request.form.get("cantidad", 0))
                    cat_id = request.form.get("categoria_id")
                    productos.editar_producto(pid, nombre, precio, cantidad, cat_id, session["usuario"]["username"])
                    flash(f"Producto actualizado.", "success")
                elif action == "eliminar":
                    pid = request.form.get("producto_id")
                    productos.eliminar_producto(pid, session["usuario"]["username"])
                    flash("Producto eliminado.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
            return redirect(url_for("inventario"))

        return render_template("inventario.html", productos=prods, categorias=cats, cat_map=cat_map)

    @app.route("/inventario/excel")
    @login_required
    def inventario_excel_descargar():
        import platform
        import subprocess
        from pathlib import Path
        from backend.productos import exportar_inventario_excel

        data = exportar_inventario_excel()

        carpeta = Path.home() / "Documents" / "ElectroGalindez Inventario"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / "inventario_completo.xlsx"

        with open(ruta, "wb") as f:
            f.write(data)

        try:
            sistema = platform.system()
            if sistema == "Darwin":
                subprocess.Popen(["open", str(ruta)])
            elif sistema == "Windows":
                os.startfile(str(ruta))
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as e:
            return jsonify({"ok": False, "error": f"Excel guardado en {ruta} pero no se pudo abrir: {e}"})

        return jsonify({"ok": True, "archivo": str(ruta)})

    @app.route("/ventas", methods=["GET", "POST"])
    @login_required
    def registrar_venta():
        clis = clientes.list_clients() or []
        prods = productos.list_products() or []

        if request.method == "POST":
            action = request.form.get("action")
            venta_registrada = None
            try:
                if action == "registrar":
                    cliente_id = int(request.form.get("cliente_id"))
                    items_json = request.form.get("items", "[]")
                    items = json.loads(items_json)
                    total = float(request.form.get("total", 0))
                    pagado = float(request.form.get("pagado", 0))
                    tipo_pago = request.form.get("tipo_pago", "Efectivo")
                    # el gestor es opcional: la venta puede venir sin gestor
                    gestor_id = request.form.get("gestor_id") or None

                    nueva = ventas.register_sale(
                        cliente_id=cliente_id, total=total, pagado=pagado,
                        usuario=session["usuario"]["username"],
                        tipo_pago=tipo_pago, productos=items, gestor_id=gestor_id
                    )
                    venta_registrada = nueva["id"]

                    if pagado < total:
                        saldo = total - pagado
                        deudas.add_debt(
                            cliente_id=cliente_id, monto_total=saldo,
                            venta_id=nueva["id"], productos=items,
                            usuario=session["usuario"]["username"]
                        )
                        flash(f"Venta #{nueva['id']} registrada con deuda de ${saldo:,.2f}", "info")
                    else:
                        flash(f"Venta #{nueva['id']} registrada. Total: ${total:,.2f}", "success")

                elif action == "eliminar":
                    venta_id = request.form.get("venta_id")
                    ventas.delete_sale(venta_id, session["usuario"]["username"])
                    flash("Venta eliminada y stock restaurado.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
            if action == "eliminar":
                fi, ff, _ = ventas.normalizar_rango(
                    request.form.get("fecha_inicio"), request.form.get("fecha_fin")
                )
                return redirect(url_for("registrar_venta",
                                        fecha_inicio=fi.isoformat(),
                                        fecha_fin=ff.isoformat()))
            # Tras registrar, ?factura=<id> hace que la pagina abra sola la
            # factura de la venta recien creada.
            if venta_registrada:
                return redirect(url_for("registrar_venta", factura=venta_registrada))
            return redirect(url_for("registrar_venta"))

        page = max(int(request.args.get("page", 1)), 1)
        per_page = 50

        fi, ff, invertido = ventas.normalizar_rango(
            request.args.get("fecha_inicio"), request.args.get("fecha_fin")
        )
        fecha_inicio, fecha_fin = fi.isoformat(), ff.isoformat()
        if invertido:
            flash("La fecha de inicio no puede ser mayor que la fecha final.", "warning")

        offset = (page - 1) * per_page
        ventas_list = ventas.list_sales(
            limit=per_page, offset=offset,
            fecha_inicio=fi, fecha_fin=ff,
            orden="desc", ordenar_por="id",
        ) or []
        total_ventas = ventas.count_sales(fecha_inicio=fi, fecha_fin=ff)
        total_pages = (total_ventas + per_page - 1) // per_page
        if page > total_pages and total_pages >= 1:
            return redirect(url_for("registrar_venta", fecha_inicio=fecha_inicio,
                                    fecha_fin=fecha_fin, page=total_pages))
        clientes_map = {c["id"]: c["nombre"] for c in clis}

        clientes_selector = clientes.clientes_para_selector()

        return render_template("ventas.html",
            productos=prods,
            ventas=ventas_list, clientes_map=clientes_map,
            page=page, total_pages=total_pages, total_ventas=total_ventas,
            fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
            clientes_selector=clientes_selector,
            gestores=clientes.listar_gestores(),
            total_clientes=len(clientes_selector),
            ultima_venta=ventas.ultima_venta(),
            factura_pendiente=request.args.get("factura", type=int),
        )

    @app.route("/ventas/buscar")
    @login_required
    def ventas_buscar():
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"rows": []})
        fi, ff, _ = ventas.normalizar_rango(
            request.args.get("fecha_inicio"), request.args.get("fecha_fin")
        )
        rows = []
        for v in ventas.search_sales(q, fecha_inicio=fi, fecha_fin=ff):
            estado = "Pagada" if float(v.get("pagado") or 0) >= float(v.get("total") or 0) else "Pendiente"
            fecha = v.get("fecha")
            fecha_str = fecha.strftime('%d/%m/%Y %H:%M') if hasattr(fecha, "strftime") else (str(fecha) if fecha else "")
            rows.append({
                "id": v.get("id"),
                "fecha": fecha_str,
                "cliente": v.get("cliente_nombre") or "N/A",
                "gestor": v.get("gestor_nombre") or "",
                "total": float(v.get("total") or 0),
                "pagado": float(v.get("pagado") or 0),
                "estado": estado,
                "productos": _productos_venta(v.get("productos_vendidos")),
            })
        return jsonify({"rows": rows})

    @app.route("/ventas_dia", methods=["GET", "POST"])
    @login_required
    def ventas_dia():
        clis = {c["id"]: c for c in (clientes.list_clients() or [])}

        fi, ff, invertido = ventas.normalizar_rango(
            request.args.get("fecha_inicio"), request.args.get("fecha_fin")
        )
        fecha_inicio, fecha_fin = fi.isoformat(), ff.isoformat()
        if invertido:
            flash("La fecha de inicio no puede ser mayor que la fecha final.", "warning")

        # Igual que Ventas Registradas: de la venta mas reciente a la mas vieja.
        vens = ventas.list_sales(fecha_inicio=fi, fecha_fin=ff,
                                 orden="desc", ordenar_por="id") or []

        filas = []
        for v in vens:
            vfecha = v.get("fecha")
            if not hasattr(vfecha, "date"):
                continue
            cliente = clis.get(v.get("cliente_id"), {"nombre": "Desconocido"})
            estado = "Pagada" if float(v.get("pagado", 0)) >= float(v.get("total", 0)) else "Pendiente"
            for p in (v.get("productos_vendidos") or []):
                filas.append({
                    "id_venta": v.get("id"),
                    "fecha": vfecha.strftime("%d/%m/%Y %H:%M") if hasattr(vfecha, "strftime") else str(vfecha),
                    "cliente": cliente.get("nombre", "N/A"),
                    "producto": p.get("nombre", ""),
                    "cantidad": int(p.get("cantidad", 0)),
                    "precio_unitario": float(p.get("precio_unitario", 0)),
                    "subtotal": float(p.get("subtotal", 0)),
                    "total": float(v.get("total", 0)),
                    "pagado": float(v.get("pagado", 0)),
                    "saldo": max(float(v.get("total", 0)) - float(v.get("pagado", 0)), 0),
                    "estado": estado
                })

        total_pagado = sum(f["pagado"] for f in filas)
        total_pendiente = sum(f["saldo"] for f in filas)

        # Resumen de productos vendidos
        prod_resumen = {}
        for f in filas:
            nombre = f.get("producto", "")
            prod_resumen[nombre] = prod_resumen.get(nombre, 0) + f["cantidad"]
        productos_resumen = sorted(
            [{"producto": k, "cantidad": v} for k, v in prod_resumen.items()],
            key=lambda x: x["cantidad"], reverse=True
        )

        return render_template("ventas_dia.html",
            filas=filas, fecha_inicio=fecha_inicio, fecha_fin=fecha_fin,
            total_pagado=total_pagado, total_pendiente=total_pendiente,
            total_ventas=len(set(f["id_venta"] for f in filas)),
            total_productos_vendidos=sum(f["cantidad"] for f in filas),
            productos_resumen=productos_resumen
        )

    @app.route("/ventas_dia/excel")
    @login_required
    def ventas_dia_excel():
        import platform
        import subprocess
        from pathlib import Path

        fi, ff, _ = ventas.normalizar_rango(
            request.args.get("fecha_inicio"), request.args.get("fecha_fin")
        )
        fecha_inicio, fecha_fin = fi.isoformat(), ff.isoformat()

        data = ventas.exportar_ventas_dia_excel(fecha_inicio, fecha_fin)

        carpeta = Path.home() / "Documents" / "ElectroGalindez Ventas"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / f"ventas_{fecha_inicio}_a_{fecha_fin}.xlsx"

        with open(ruta, "wb") as f:
            f.write(data)

        try:
            sistema = platform.system()
            if sistema == "Darwin":
                subprocess.Popen(["open", str(ruta)])
            elif sistema == "Windows":
                os.startfile(str(ruta))
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as e:
            return jsonify({"ok": False, "error": f"Excel guardado en {ruta} pero no se pudo abrir: {e}"})

        return jsonify({"ok": True, "archivo": str(ruta)})

    @app.route("/deudas", methods=["GET", "POST"])
    @login_required
    def deudas_page():
        clientes_con_deuda = deudas.list_clientes_con_deuda() or []
        prod_map = productos.map_productos() or {}
        clientes_dict = {c["id"]: c["nombre"] for c in (clientes.list_clients() or [])}
        detalles_totales = deudas.list_detalle_deudas() or []

        selected_cliente = request.args.get("cliente_id")
        cliente_obj = None
        deudas_cliente = []
        filas_pendientes = []

        if selected_cliente:
            cliente_obj = clientes.get_client(int(selected_cliente))
            deudas_cliente = deudas.debts_by_client(int(selected_cliente)) or []
            for d in deudas_cliente:
                for det in d.get("detalles", []):
                    if (det.get("estado") or "").lower() != "pendiente":
                        continue
                    cant = float(det.get("cantidad", 0))
                    precio = float(det.get("precio_unitario", 0))
                    filas_pendientes.append({
                        "deuda_id": d.get("deuda_id"),
                        "detalle_id": det.get("id"),
                        "producto_id": det.get("producto_id"),
                        "producto": prod_map.get(det.get("producto_id"), "Producto"),
                        "cantidad": cant,
                        "precio_unitario": precio,
                        "monto_pendiente": round(cant * precio, 2),
                        "fecha": str(d.get("fecha", ""))[:19],
                    })

        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "pagar":
                    deuda_id = int(request.form.get("deuda_id"))
                    producto_id = int(request.form.get("producto_id"))
                    monto = float(request.form.get("monto_pago", 0))
                    metodo = request.form.get("metodo_pago", "Efectivo")
                    deudas.pay_debt_producto(deuda_id, producto_id, monto, session["usuario"]["username"], metodo_pago=metodo)
                    flash(f"Pago de ${monto:,.2f} registrado.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
            cid = request.args.get("cliente_id", "")
            return redirect(url_for("deudas_page", cliente_id=cid))

        filas_general = []
        for d in detalles_totales:
            if str(d.get("estado", "pendiente")).lower() != "pendiente":
                continue
            cant = float(d.get("cantidad", 0))
            precio = float(d.get("precio_unitario", 0))
            filas_general.append({
                "cliente": clientes_dict.get(d.get("cliente_id"), "Desconocido"),
                "deuda_id": d.get("deuda_id"),
                "producto": prod_map.get(d.get("producto_id"), "Producto"),
                "cantidad": cant,
                "precio_unitario": precio,
                "monto_total": round(cant * precio, 2),
                "fecha": str(d.get("fecha", ""))[:19],
            })

        pagos_recientes = deudas.list_pagos(selected_cliente if selected_cliente else None) or []
        for p in pagos_recientes:
            p["fecha_str"] = str(p.get("fecha", ""))[:19]

        total_pendiente = round(sum(float(f["monto_total"]) for f in filas_general), 2)

        return render_template("deudas.html",
            clientes_con_deuda=clientes_con_deuda,
            cliente_obj=cliente_obj,
            filas_pendientes=filas_pendientes,
            filas_general=filas_general,
            selected_cliente=selected_cliente,
            prod_map=prod_map,
            pagos_recientes=pagos_recientes,
            total_pendiente=total_pendiente,
        )

    @app.route("/deudas/buscar")
    @login_required
    def deudas_buscar_clientes():
        """Buscador del selector de /deudas, igual que el de /clientes.

        Busca sobre TODOS los clientes, no solo los que tienen deuda, y ordena
        el ID exacto primero. Antes la lista solo traia clientes con deuda
        filtrada por clientes.deuda_total, una columna que se desactualiza, y
        filtrar en el navegador no encontraba a quien no estuviera ya en ella.

        El importe pendiente se recalcula desde deudas_detalle para no mostrar
        la columna deuda_total.
        """
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"rows": [], "total": 0, "mostrando": 0})

        pendientes = deudas.deuda_pendiente_por_cliente()
        filas = clientes.search_clients(q, limit=100) or []
        return jsonify({
            "rows": [{
                "id": c.get("id"),
                "nombre": c.get("nombre") or "",
                "telefono": c.get("telefono") or "",
                "ci": c.get("ci") or "",
                "chapa": c.get("chapa") or "",
                "deuda": float(pendientes.get(int(c["id"]), 0) or 0),
            } for c in filas],
            "total": len(filas),
            "mostrando": len(filas),
        })

    @app.route("/deudas/excel")
    @login_required
    def deudas_excel_descargar():
        import platform
        import subprocess
        from pathlib import Path
        from backend.deudas import exportar_deudas_excel

        cliente_id = request.args.get("cliente_id")
        if cliente_id:
            data = exportar_deudas_excel(int(cliente_id))
            base = f"deudas_cliente_{cliente_id}.xlsx"
            subtitulo = "cliente"
        else:
            data = exportar_deudas_excel()
            base = "todas_las_deudas.xlsx"
            subtitulo = "todas"

        carpeta = Path.home() / "Documents" / "ElectroGalindez Deudas"
        carpeta.mkdir(parents=True, exist_ok=True)
        ruta = carpeta / base
        with open(ruta, "wb") as f:
            f.write(data)

        try:
            sistema = platform.system()
            if sistema == "Darwin":
                subprocess.Popen(["open", str(ruta)])
            elif sistema == "Windows":
                os.startfile(str(ruta))
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as e:
            return jsonify({"ok": False, "error": f"Excel guardado en {ruta} pero no se pudo abrir: {e}"})

        return jsonify({"ok": True, "archivo": str(ruta), "subtitulo": subtitulo})

    @app.route("/deudas/comprobante/<int:pago_id>/descargar")
    @login_required
    def deudas_comprobante_descargar(pago_id):
        import platform
        import subprocess
        from pathlib import Path
        from backend.deudas import generar_comprobante_pago, get_pago

        pago = get_pago(pago_id)
        if not pago:
            return jsonify({"ok": False, "error": "Pago no encontrado"}), 404

        buffer = generar_comprobante_pago(pago_id, session.get("usuario", {}).get("username", ""))
        if buffer is None:
            return jsonify({"ok": False, "error": "No se pudo generar el comprobante"}), 500

        carpeta = Path.home() / "Documents" / "ElectroGalindez Deudas" / "Comprobantes"
        carpeta.mkdir(parents=True, exist_ok=True)
        fecha_str = str(pago.get("fecha", ""))[:10].replace("-", "")
        filename = f"ComprobantePago_Deuda{pago['deuda_id']}_{pago_id}_{fecha_str}.pdf"
        ruta = carpeta / filename

        with open(ruta, "wb") as f:
            f.write(buffer)

        try:
            sistema = platform.system()
            if sistema == "Darwin":
                subprocess.Popen(["open", str(ruta)])
            elif sistema == "Windows":
                os.startfile(str(ruta))
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as e:
            return jsonify({"ok": False, "error": f"Comprobante guardado en {ruta} pero no se pudo abrir: {e}"})

        return jsonify({"ok": True, "archivo": str(ruta)})

    @app.route("/categorias", methods=["GET", "POST"])
    @admin_required
    def categorias_page():
        cats = categorias.list_categories() or []
        prods = productos.list_products() or []

        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "crear":
                    nombre = request.form.get("nombre", "").strip()
                    categorias.agregar_categoria(nombre, session["usuario"]["username"])
                    flash(f"Categoria '{nombre}' creada.", "success")
                elif action == "editar":
                    cat_id = int(request.form.get("cat_id"))
                    nombre = request.form.get("nombre", "").strip()
                    categorias.editar_categoria(cat_id, nombre, session["usuario"]["username"])
                    flash("Categoria actualizada.", "success")
                elif action == "eliminar":
                    cat_id = int(request.form.get("cat_id"))
                    categorias.eliminar_categoria(cat_id, session["usuario"]["username"])
                    flash("Categoria eliminada.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
            return redirect(url_for("categorias_page"))

        return render_template("categorias.html", categorias=cats, productos=prods)

    @app.route("/clientes", methods=["GET", "POST"])
    @login_required
    def clientes_page():
        if request.method == "POST":
            # El formulario manda fetch para poder avisar a las demás vistas
            # (/ventas) del cambio. Con navegadores sin JS sigue siendo un
            # POST normal y redirige como siempre.
            ajax = request.headers.get("X-Requested-With") == "fetch"
            action = request.form.get("action")
            try:
                if action == "crear":
                    nombre = request.form.get("nombre", "").strip()
                    telefono = request.form.get("telefono", "")
                    ci = request.form.get("ci", "")
                    chapa = request.form.get("chapa", "")
                    direccion = request.form.get("direccion", "")
                    nuevo = clientes.add_client(nombre, telefono, ci, direccion, chapa, session["usuario"]["username"])
                    flash(f"Cliente '{nombre}' creado.", "success")
                    if ajax:
                        return jsonify({"ok": True, "accion": "crear", "cliente": nuevo})
                elif action == "editar":
                    cid = request.form.get("cliente_id")
                    nombre = request.form.get("nombre", "")
                    telefono = request.form.get("telefono", "")
                    ci = request.form.get("ci", "")
                    chapa = request.form.get("chapa", "")
                    direccion = request.form.get("direccion", "")
                    clientes.update_client(cid, nombre=nombre, telefono=telefono, ci=ci, chapa=chapa, direccion=direccion, usuario=session["usuario"]["username"])
                    flash("Cliente actualizado.", "success")
                    if ajax:
                        return jsonify({"ok": True, "accion": "editar",
                                        "cliente": clientes.get_client(int(cid))})
                elif action == "eliminar":
                    cid = request.form.get("cliente_id")
                    clientes.delete_client(cid, session["usuario"]["username"])
                    flash("Cliente eliminado.", "success")
                    if ajax:
                        return jsonify({"ok": True, "accion": "eliminar",
                                        "cliente": {"id": int(cid)}})
            except Exception as e:
                if ajax:
                    return jsonify({"ok": False, "error": str(e)}), 400
                flash(f"Error: {e}", "danger")
            return redirect(url_for("clientes_page"))

        page = max(int(request.args.get("page", 1)), 1)
        per_page = 100
        q = (request.args.get("q") or "").strip()

        if q:
            # Búsqueda real sobre TODOS los clientes, no solo los de la página.
            offset = (page - 1) * per_page
            clis = clientes.search_clients(q, limit=per_page, offset=offset) or []
            total_clients = clientes.count_clients_search(q)
        else:
            offset = (page - 1) * per_page
            clis = clientes.list_clients(limit=per_page, offset=offset) or []
            total_clients = clientes.count_clients()
        total_pages = (total_clients + per_page - 1) // per_page

        return render_template("clientes.html", clientes=clis, page=page,
                               total_pages=total_pages, total_clients=total_clients,
                               q=q)

    # ======================================================
    # 🧑‍💼 Gestores: clientes marcados para cobrar comisión
    # ======================================================
    @app.route("/gestores", methods=["GET", "POST"])
    @login_required
    def gestores_page():
        """Panel para marcar de todos los clientes cuales son gestores.

        Un gestor sigue siendo un cliente normal: es solo una bandera, asi que
        puede comprar, deber y aparecer en el resto de la app igual que todos.
        """
        ajax = request.headers.get("X-Requested-With") == "fetch"
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action in ("marcar", "desmarcar"):
                    cliente_id = request.form.get("cliente_id")
                    cliente = clientes.set_gestor(
                        cliente_id, action == "marcar", session["usuario"]["username"])
                    if ajax:
                        return jsonify({"ok": True, "cliente": cliente,
                                        "gestor": bool(cliente.get("es_gestor"))})
                elif action in ("marcar_varios", "desmarcar_varios"):
                    ids = request.form.getlist("cliente_ids") or \
                        json.loads(request.form.get("cliente_ids") or "[]")
                    total = clientes.set_gestores_masivo(
                        ids, action == "marcar_varios", session["usuario"]["username"])
                    if ajax:
                        return jsonify({"ok": True, "actualizados": total})
            except Exception as e:
                if ajax:
                    return jsonify({"ok": False, "error": str(e)}), 400
                flash(f"Error: {e}", "danger")
            return redirect(url_for("gestores_page"))

        q = (request.args.get("q") or "").strip()
        solo_gestores = request.args.get("vista", "") == "gestores"
        ver_todo = request.args.get("todo") == "1"
        fi, ff, _ = ventas.normalizar_rango(
            request.args.get("fecha_inicio"), request.args.get("fecha_fin"))
        # por defecto el panel mira solo el dia, como /ventas. Con todo=1 se
        # saca el rango para poder revisar el historial completo de un gestor.

        if q:
            lista = clientes.search_clients(q) or []
        else:
            lista = clientes.list_clients() or []
        if solo_gestores:
            lista = [c for c in lista if c.get("es_gestor")]

        # el rango va en la URL asi el filtro sobrevive al buscador y al refresco
        resumen = (ventas.resumen_por_gestor() if ver_todo
                   else ventas.resumen_por_gestor(fi, ff))

        # los gestores ya quedan filtrados arriba, pero el buscador propio del
        # panel los recorre todos para que no se pierdan al escribir
        return render_template(
            "gestores.html", clientes=lista, q=q, solo_gestores=solo_gestores,
            total_gestores=clientes.count_gestores(),
            total_clientes=clientes.count_clients(),
            resumen_gestores=resumen,
            fecha_inicio=fi.isoformat(), fecha_fin=ff.isoformat(),
            ver_todo=ver_todo)

    @app.route("/api/gestores/<int:gestor_id>/ventas")
    @login_required
    def api_ventas_de_gestor(gestor_id: int):
        """Ventas que trajo un gestor, para abrirla desde el panel.

        Se pide bajo demanda y no embebido en /gestores: el panel lista todos
        los clientes y con miles de ventas el HTML se volveria enorme.
        """
        gestor = clientes.get_client(gestor_id)
        if not gestor:
            return jsonify({"ok": False, "error": "El cliente no existe."}), 404
        ver_todo = request.args.get("todo") == "1"
        if ver_todo:
            fi = ff = None
        else:
            fi, ff, _ = ventas.normalizar_rango(
                request.args.get("fecha_inicio"), request.args.get("fecha_fin"))
        return jsonify({
            "ok": True,
            "gestor": {"id": gestor["id"], "nombre": gestor["nombre"]},
            "ver_todo": ver_todo,
            "ventas": ventas.ventas_de_gestor(gestor_id,
                                              fecha_inicio=fi, fecha_fin=ff),
        })

    @app.route("/api/gestores", methods=["POST"])
    @login_required
    def api_gestores():
        """Marca o desmarca gestores (uno o varios) sin recargar la pagina."""
        data = request.get_json(silent=True) or {}
        ids = data.get("cliente_ids") or ([data["cliente_id"]] if data.get("cliente_id") else [])
        es_gestor = clientes._a_bool(data.get("es_gestor"))
        total = clientes.set_gestores_masivo(ids, es_gestor, session["usuario"]["username"])
        return jsonify({"ok": True, "actualizados": total,
                        "total_gestores": clientes.count_gestores()})


    @app.route("/ventas/clientes")
    @login_required
    def ventas_clientes():
        """Lista de clientes para refrescar el selector de /ventas.

        El selector guarda una copia de los clientes en el navegador; este
        endpoint permite recargarla cuando el cliente se editó en otro módulo.
        """
        return jsonify({"clientes": clientes.clientes_para_selector()})

    @app.route("/clientes/buscar")
    @login_required
    def clientes_buscar():
        """Búsqueda de clientes sobre todos, para el filtro en vivo de /clientes.

        Usa el mismo criterio que el selector de /ventas, así un cliente que
        aparece en un módulo aparece en el otro.
        """
        q = (request.args.get("q") or "").strip()
        if not q:
            return jsonify({"rows": [], "total": 0, "mostrando": 0})
        page = max(int(request.args.get("page", 1)), 1)
        per_page = 100
        total = clientes.count_clients_search(q)
        filas = clientes.search_clients(q, limit=per_page,
                                        offset=(page - 1) * per_page) or []
        return jsonify({
            "rows": [{
                "id": c.get("id"),
                "nombre": c.get("nombre") or "",
                "telefono": c.get("telefono") or "",
                "ci": c.get("ci") or "",
                "chapa": c.get("chapa") or "",
                "direccion": c.get("direccion") or "",
                "deuda": float(c.get("deuda_total") or 0),
            } for c in filas],
            "total": total,
            "mostrando": len(filas),
        })

    @app.route("/usuarios", methods=["GET", "POST"])
    @admin_required
    def usuarios_page():
        users = usuarios.listar_usuarios() or []

        if request.method == "POST":
            action = request.form.get("action")
            actor = session["usuario"]["username"]
            try:
                if action == "crear":
                    username = request.form.get("username", "").strip()
                    password = request.form.get("password", "")
                    rol = request.form.get("rol", "empleado")
                    usuarios.crear_usuario(username, password, rol, actor=actor)
                    flash(f"Usuario '{username}' creado.", "success")
                elif action == "cambiar_rol":
                    username = request.form.get("username")
                    nuevo_rol = request.form.get("rol")
                    usuarios.cambiar_rol(username, nuevo_rol, actor=actor)
                    flash("Rol actualizado.", "success")
                elif action == "cambiar_estado":
                    username = request.form.get("username")
                    activo = request.form.get("activo") == "true"
                    if activo:
                        usuarios.activar_usuario(username, actor=actor)
                    else:
                        usuarios.desactivar_usuario(username, actor=actor)
                    flash("Estado actualizado.", "success")
                elif action == "cambiar_password":
                    username = request.form.get("username")
                    new_pass = request.form.get("new_password", "")
                    if new_pass:
                        usuarios.cambiar_password(username, new_pass, actor=actor)
                        flash("Contrasena actualizada.", "success")
                elif action == "eliminar":
                    username = request.form.get("username")
                    usuarios.eliminar_usuario(username, actor=actor)
                    flash("Usuario eliminado.", "success")
            except Exception as e:
                flash(f"Error: {e}", "danger")
            return redirect(url_for("usuarios_page"))

        return render_template("usuarios.html", usuarios=users)

    @app.route("/logs")
    @admin_required
    def logs_page():
        page = max(int(request.args.get("page", 1)), 1)
        per_page = 200
        offset = (page - 1) * per_page
        logs_data = logs.listar_logs(limit=per_page, offset=offset) or []
        total_logs = logs.contar_logs()
        total_pages = (total_logs + per_page - 1) // per_page
        return render_template("logs.html", logs=logs_data, page=page, total_pages=total_pages, total_logs=total_logs)

    @app.route("/historial")
    @admin_required
    def historial_page():
        return render_template("historial.html")

    @app.route("/api/productos")
    @login_required
    def api_productos():
        prods = productos.list_products() or []
        return jsonify(prods)

    @app.route("/api/clientes", methods=["GET"])
    @login_required
    def api_clientes():
        clis = clientes.list_clients() or []
        return jsonify(clis)

    @app.route("/api/clientes", methods=["POST"])
    @login_required
    def api_crear_cliente():
        data = request.get_json() or {}
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "Nombre requerido"}), 400
        telefono = data.get("telefono", "")
        ci = data.get("ci", "")
        chapa = data.get("chapa", "")
        direccion = data.get("direccion", "")
        actor = session["usuario"]["username"]
        nuevo = clientes.add_client(nombre, telefono, ci, direccion, chapa, actor)
        return jsonify({"ok": True, "cliente": nuevo})

    @app.route("/api/clientes/<int:cliente_id>", methods=["GET"])
    @login_required
    def api_obtener_cliente(cliente_id):
        """Cliente completo, para el modal de edicion rapida de /ventas.

        La copia que hay en el navegador no trae la direccion, asi que al
        editar se pide el dato real y no se pisa nada.
        """
        cli = clientes.get_client(cliente_id)
        if not cli:
            return jsonify({"error": "Cliente no encontrado"}), 404
        return jsonify({"ok": True, "cliente": cli})

    @app.route("/api/clientes/<int:cliente_id>/deudas")
    @login_required
    def api_clientes_deudas(cliente_id):
        """Deudas pendientes del cliente, para consultarlas sin salir de /ventas.

        Las ya pagadas no se devuelven: en /ventas solo interesa lo que falta.
        """
        cli = clientes.get_client(cliente_id)
        if not cli:
            return jsonify({"error": "Cliente no encontrado"}), 404
        filas = deudas.pendientes_por_cliente(cliente_id) or []
        return jsonify({
            "ok": True,
            "cliente": {"id": cli["id"], "nombre": cli["nombre"]},
            "total": len(filas),
            "deudas": filas,
        })

    @app.route("/api/clientes/<int:cliente_id>", methods=["PUT", "POST"])
    @login_required
    def api_actualizar_cliente(cliente_id):
        """Edicion rapida de cliente desde /ventas, sin salir del modulo."""
        data = request.get_json() or {}
        nombre = (data.get("nombre") or "").strip()
        if not nombre:
            return jsonify({"error": "Nombre requerido"}), 400
        try:
            actualizado = clientes.update_client(
                cliente_id, nombre=nombre,
                telefono=data.get("telefono", ""), ci=data.get("ci", ""),
                chapa=data.get("chapa", ""), direccion=data.get("direccion", ""),
                usuario=session["usuario"]["username"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        return jsonify({"ok": True, "cliente": actualizado})

    @app.route("/api/ventas/<int:venta_id>/datos_factura")
    @login_required
    def api_venta_datos_factura(venta_id):
        v = ventas.get_sale(venta_id)
        if not v:
            return jsonify({"error": "Venta no encontrada"}), 404
        cli = clientes.get_client(v["cliente_id"]) or {}
        return jsonify({
            "venta": {
                "id": v["id"],
                "fecha": v["fecha"].strftime("%d/%m/%Y %H:%M") if hasattr(v["fecha"], "strftime") else str(v["fecha"]),
                "total": v["total"],
                "pagado": v["pagado"],
                "saldo": v["saldo"],
                "tipo_pago": v.get("tipo_pago"),
                "usuario": v.get("usuario"),
                "vendedor": v.get("vendedor"),
                "telefono_vendedor": v.get("telefono_vendedor"),
                "chofer": v.get("chofer"),
                "chapa": v.get("chapa"),
                "observaciones": v.get("observaciones"),
            },
            "cliente": {
                "nombre": cli.get("nombre"),
                "ci": cli.get("ci"),
                "direccion": cli.get("direccion"),
                "telefono": cli.get("telefono"),
                "chapa": cli.get("chapa"),
            },
        })

    @app.route("/api/ventas/<int:venta_id>/factura_datos", methods=["POST"])
    @login_required
    def api_venta_guardar_factura_datos(venta_id):
        data = request.get_json() or {}
        ok = ventas.actualizar_factura_datos(
            sale_id=venta_id,
            observaciones=(data.get("observaciones") or "").strip() or None,
            vendedor=(data.get("vendedor") or "").strip() or None,
            telefono_vendedor=(data.get("telefono_vendedor") or "").strip() or None,
            chofer=(data.get("chofer") or "").strip() or None,
            chapa=(data.get("chapa") or "").strip() or None,
            usuario=session["usuario"]["username"],
        )
        if not ok:
            return jsonify({"ok": False, "error": "Venta no encontrada"}), 404
        return jsonify({"ok": True})

    @app.route("/factura/<int:venta_id>")
    @login_required
    def factura_pdf(venta_id):
        from backend.facturas import generar_factura

        v = ventas.get_sale(venta_id)
        if not v:
            flash("Venta no encontrada", "danger")
            return redirect(url_for("registrar_venta"))

        buffer = generar_factura(venta_id, session.get("usuario", {}).get("username", ""))
        from flask import send_file
        return send_file(buffer, mimetype="application/pdf",
                         as_attachment=True,
                         download_name=f"factura_venta_{v['id']}.pdf")

    @app.route("/factura/<int:venta_id>/descargar", methods=["GET"])
    @login_required
    def factura_descargar(venta_id):
        import platform
        import shutil
        import subprocess
        from pathlib import Path
        from backend.facturas import generar_factura

        v = ventas.get_sale(venta_id)
        if not v:
            return jsonify({"ok": False, "error": "Venta no encontrada"}), 404

        buffer = generar_factura(venta_id, session.get("usuario", {}).get("username", ""))
        if buffer is None:
            return jsonify({"ok": False, "error": "No se pudo generar la factura"}), 500

        carpeta = Path.home() / "Documents" / "ElectroGalindez Facturas"
        carpeta.mkdir(parents=True, exist_ok=True)
        filename = f"Factura_Venta_{v['id']}_{v['fecha'].strftime('%d-%m-%Y') if hasattr(v['fecha'], 'strftime') else ''}.pdf"
        ruta = carpeta / filename

        with open(ruta, "wb") as f:
            f.write(buffer.getvalue())

        try:
            sistema = platform.system()
            if sistema == "Darwin":
                subprocess.Popen(["open", str(ruta)])
            elif sistema == "Windows":
                os.startfile(str(ruta))
            else:
                subprocess.Popen(["xdg-open", str(ruta)])
        except Exception as e:
            return jsonify({"ok": False, "error": f"Factura guardada en {ruta} pero no se pudo abrir: {e}"})

        return jsonify({"ok": True, "archivo": str(ruta)})

    # ======================================================
    # ☁️ Copia en Neon (para ver la app desde otro lugar)
    # ======================================================
    @app.route("/sync/estado")
    @login_required
    def sync_estado():
        return jsonify({"activa": sync_neon.configurada(), **sync_neon.leer_estado()})

    @app.route("/sync/ahora", methods=["POST"])
    @admin_required
    def sync_ahora():
        """Sube ya, sin esperar al proximo turno. Corre aparte para no
        congelar la pantalla mientras sube."""
        if not sync_neon.configurada():
            return jsonify({"ok": False, "error": "Neon no esta configurado."}), 400
        import threading
        threading.Thread(target=sync_neon.sincronizar, kwargs={"forzar": True},
                         daemon=True).start()
        return jsonify({"ok": True})

    @app.route("/manifest.json")
    def manifest():
        from backend.app_meta import APP_NAME, APP_DESCRIPTION
        return jsonify({
            "name": APP_NAME,
            "short_name": APP_NAME,
            "description": APP_DESCRIPTION,
            "start_url": url_for("dashboard"),
            "scope": "/",
            "display": "standalone",
            "orientation": "portrait-primary",
            "background_color": "#1c2a3a",
            "theme_color": "#2E75B6",
            "icons": [
                {"src": url_for("static", filename="icons/icon-192.png", _external=True),
                 "sizes": "192x192", "type": "image/png", "purpose": "any"},
                {"src": url_for("static", filename="icons/icon-512.png", _external=True),
                 "sizes": "512x512", "type": "image/png", "purpose": "any"},
                {"src": url_for("static", filename="icons/maskable-192.png", _external=True),
                 "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
                {"src": url_for("static", filename="icons/maskable-512.png", _external=True),
                 "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
            ],
        })

    @app.route("/sw.js")
    def sw():
        from flask import send_from_directory
        return send_from_directory(os.path.join(app.static_folder, "pwa"), "sw.js",
                                   mimetype="application/javascript")

    # Usuario admin por defecto para poder entrar (no pisa uno existente).
    try:
        usuarios.asegurar_admin("admin", "admin1234")
    except Exception as e:
        app.logger.warning("No se pudo asegurar el usuario admin: %s", e)

    return app


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="ContaStock Pro - Sistema de contabilidad e inventario")
    parser.add_argument("--host", default="127.0.0.1", help="IP donde escuchar (0.0.0.0 para red local/PWA)")
    parser.add_argument("--port", type=int, default=5555, help="Puerto del servidor")
    parser.add_argument("--debug", action="store_true", help="Modo debug (recarga automatica)")
    args = parser.parse_args()

    application = create_app()
    # con --debug el reloader arranca dos procesos: que suba solo el hijo
    if not args.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        from backend import sync_neon
        sync_neon.iniciar_automatica()
    application.run(host=args.host, port=args.port, debug=args.debug)
