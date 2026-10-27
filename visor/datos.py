"""Consultas del visor: solo lectura, sobre la copia de Neon.

Este módulo no importa nada de `backend/` a propósito: el visor se despliega
solo (en un hosting), sin la app completa, y `backend.db` crea tablas al
importarse, algo que el visor nunca debe hacer.

Las consultas son SQL portable (PostgreSQL y SQLite) para poder probar el
visor sobre la base local. Las fechas de la base son la hora local del
almacén, sin zona horaria; "hoy" se calcula en la zona VISOR_TZ.
"""
import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

TOLERANCIA = 0.01
STOCK_BAJO = 5
ZONA = ZoneInfo(os.getenv("VISOR_TZ", "America/Havana"))

_engine = None


def url_base() -> str:
    url = os.getenv("VISOR_DATABASE_URL") or os.getenv("NEON_DATABASE_URL") or ""
    if not url:
        raise RuntimeError("Falta VISOR_DATABASE_URL (la URL de Neon).")
    # Neon da URLs "postgres://"; SQLAlchemy quiere "postgresql://"
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    return url


def engine():
    global _engine
    if _engine is None:
        url = url_base()
        if url.startswith("sqlite"):
            _engine = create_engine(url, future=True)
        else:
            # sin pool: una conexión abierta puede impedir que Neon se duerma
            # y eso gasta horas del plan gratis
            _engine = create_engine(url, future=True, poolclass=NullPool,
                                    connect_args={"connect_timeout": 10})
    return _engine


def usar_engine(nuevo):
    """Para tests: apuntar el visor a otra base."""
    global _engine
    _engine = nuevo


@contextmanager
def leer():
    """Conexión de solo lectura. En Postgres la transacción se declara READ
    ONLY, así que aunque hubiera un error en el código, el visor no puede
    escribir en Neon. Se hace por transacción y no por sesión porque el pooler
    de Neon no conserva ajustes de sesión."""
    with engine().connect() as conn:
        if conn.dialect.name == "postgresql":
            conn.execute(text("SET TRANSACTION READ ONLY"))
        yield conn
        conn.rollback()


def hoy() -> date:
    return datetime.now(ZONA).date()


def _rango(desde: date, hasta: date) -> dict:
    """[desde 00:00, hasta+1 00:00) como texto: funciona igual en los dos motores."""
    return {"desde": f"{desde.isoformat()} 00:00:00",
            "hasta": f"{(hasta + timedelta(days=1)).isoformat()} 00:00:00"}


def _fecha(valor):
    if isinstance(valor, datetime):
        return valor
    try:
        return datetime.fromisoformat(str(valor))
    except (TypeError, ValueError):
        return None


def _num(valor) -> float:
    try:
        return round(float(valor or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def _productos(valor) -> list:
    if isinstance(valor, (str, bytes)):
        try:
            valor = json.loads(valor)
        except ValueError:
            return []
    if not isinstance(valor, list):
        return []
    salida = []
    for p in valor:
        if isinstance(p, dict):
            cantidad = _num(p.get("cantidad"))
            precio = _num(p.get("precio_unitario"))
            salida.append({"nombre": str(p.get("nombre") or ""), "cantidad": cantidad,
                           "precio": precio,
                           "subtotal": _num(p.get("subtotal")) or round(cantidad * precio, 2)})
    return salida


def _totales_ventas(conn, desde: date, hasta: date) -> dict:
    fila = conn.execute(text("""
        SELECT COUNT(*) AS n, COALESCE(SUM(total), 0) AS total,
               COALESCE(SUM(pagado), 0) AS pagado
        FROM ventas WHERE fecha >= :desde AND fecha < :hasta
    """), _rango(desde, hasta)).mappings().first()
    total, pagado = _num(fila["total"]), _num(fila["pagado"])
    return {"cantidad": int(fila["n"] or 0), "total": total, "pagado": pagado,
            "pendiente": round(max(total - pagado, 0), 2)}


def ultima_subida(conn):
    """Cuándo subió el almacén por última vez (tabla que escribe sync_neon)."""
    if "sync_info" not in inspect(conn).get_table_names():
        return None
    fila = conn.execute(text("SELECT * FROM sync_info WHERE id = 1")).mappings().first()
    if not fila:
        return None
    # visto_en: el almacén siguió conectado aunque no hubo cambios que subir
    fechas = [f for f in (_fecha(fila.get("subido_en")), _fecha(fila.get("visto_en"))) if f]
    if not fechas:
        return None
    # la base guarda la hora del almacén sin zona; se le agrega para que el
    # teléfono calcule bien el "hace X min" esté donde esté
    return max(fechas).replace(tzinfo=ZONA).isoformat(timespec="seconds")


# ======================================================
# Resumen
# ======================================================
def resumen(dia: date | None = None) -> dict:
    dia = dia or hoy()
    inicio_mes = dia.replace(day=1)
    desde_serie = dia - timedelta(days=29)

    with leer() as conn:
        ventas_hoy = _totales_ventas(conn, dia, dia)
        ventas_ayer = _totales_ventas(conn, dia - timedelta(days=1), dia - timedelta(days=1))
        ventas_mes = _totales_ventas(conn, inicio_mes, dia)

        deuda = conn.execute(text("""
            SELECT COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) AS total,
                   COUNT(DISTINCT d.cliente_id) AS clientes
            FROM deudas d
            JOIN deudas_detalle dd ON dd.deuda_id = d.id AND dd.estado = 'pendiente'
            WHERE d.estado = 'pendiente'
        """)).mappings().first()

        inv = conn.execute(text("""
            SELECT COUNT(*) AS productos, COALESCE(SUM(cantidad), 0) AS unidades,
                   COALESCE(SUM(cantidad * precio), 0) AS valor,
                   SUM(CASE WHEN cantidad <= :bajo AND cantidad > 0 THEN 1 ELSE 0 END) AS bajo,
                   SUM(CASE WHEN cantidad <= 0 THEN 1 ELSE 0 END) AS agotados
            FROM productos
        """), {"bajo": STOCK_BAJO}).mappings().first()

        filas = conn.execute(text("""
            SELECT fecha, total, productos_vendidos FROM ventas
            WHERE fecha >= :desde AND fecha < :hasta
        """), _rango(desde_serie, dia)).all()

        subida = ultima_subida(conn)

    # serie de 30 días y productos más vendidos del mes (en Python: es poco y
    # así no depende de funciones de fecha/JSON de cada motor)
    por_dia = {(desde_serie + timedelta(days=i)).isoformat(): 0.0 for i in range(30)}
    top = {}
    for fecha, total, productos in filas:
        f = _fecha(fecha)
        if not f:
            continue
        clave = f.date().isoformat()
        if clave in por_dia:
            por_dia[clave] = round(por_dia[clave] + _num(total), 2)
        if f.date() >= inicio_mes:
            for p in _productos(productos):
                top[p["nombre"]] = top.get(p["nombre"], 0) + p["cantidad"]

    return {
        "dia": dia.isoformat(),
        "ultima_subida": subida,
        "ventas_hoy": ventas_hoy,
        "ventas_ayer": ventas_ayer,
        "ventas_mes": ventas_mes,
        "deuda": {"total": _num(deuda["total"]), "clientes": int(deuda["clientes"] or 0)},
        "inventario": {
            "productos": int(inv["productos"] or 0), "unidades": _num(inv["unidades"]),
            "valor": _num(inv["valor"]), "stock_bajo": int(inv["bajo"] or 0),
            "agotados": int(inv["agotados"] or 0),
        },
        "serie": [{"dia": k, "total": v} for k, v in por_dia.items()],
        "top_mes": [{"nombre": k, "cantidad": v} for k, v in
                    sorted(top.items(), key=lambda x: x[1], reverse=True)[:8]],
    }


# ======================================================
# Ventas de un día
# ======================================================
def ventas_del_dia(dia: date | None = None) -> dict:
    dia = dia or hoy()
    with leer() as conn:
        filas = conn.execute(text("""
            SELECT v.id, v.fecha, v.total, v.pagado, v.tipo_pago, v.usuario,
                   v.productos_vendidos, c.nombre AS cliente, g.nombre AS gestor
            FROM ventas v
            LEFT JOIN clientes c ON c.id = v.cliente_id
            LEFT JOIN clientes g ON g.id = v.gestor_id
            WHERE v.fecha >= :desde AND v.fecha < :hasta
            ORDER BY v.id DESC
        """), _rango(dia, dia)).mappings().all()
        totales = _totales_ventas(conn, dia, dia)

    ventas = []
    for v in filas:
        total, pagado = _num(v["total"]), _num(v["pagado"])
        f = _fecha(v["fecha"])
        ventas.append({
            "id": v["id"], "hora": f.strftime("%H:%M") if f else "",
            "cliente": v["cliente"] or "Sin cliente", "gestor": v["gestor"] or "",
            "total": total, "pagado": pagado, "saldo": round(max(total - pagado, 0), 2),
            "tipo_pago": v["tipo_pago"] or "", "vendedor": v["usuario"] or "",
            "productos": _productos(v["productos_vendidos"]),
        })
    return {"dia": dia.isoformat(), "totales": totales, "ventas": ventas,
            "por_producto": resumen_por_producto(ventas)}


def resumen_por_producto(ventas: list) -> list:
    """Cuánto se vendió de cada producto en esas ventas: unidades, dinero y en
    cuántas ventas apareció. De más a menos unidades."""
    resumen = {}
    for v in ventas:
        for p in v["productos"]:
            fila = resumen.setdefault(p["nombre"] or "Sin nombre",
                                      {"cantidad": 0.0, "total": 0.0, "ventas": set()})
            fila["cantidad"] += p["cantidad"]
            fila["total"] += p["subtotal"]
            fila["ventas"].add(v["id"])
    return [{"nombre": nombre, "cantidad": round(f["cantidad"], 2),
             "total": round(f["total"], 2), "ventas": len(f["ventas"])}
            for nombre, f in sorted(resumen.items(),
                                    key=lambda x: (-x[1]["cantidad"], x[0].lower()))]


# ======================================================
# Inventario
# ======================================================
def inventario() -> dict:
    with leer() as conn:
        filas = conn.execute(text("""
            SELECT p.id, p.nombre, p.precio, p.cantidad, c.nombre AS categoria
            FROM productos p LEFT JOIN categorias c ON c.id = p.categoria_id
            ORDER BY p.nombre
        """)).mappings().all()
    productos = [{
        "id": p["id"], "nombre": p["nombre"] or "", "categoria": p["categoria"] or "",
        "precio": _num(p["precio"]), "cantidad": _num(p["cantidad"]),
        "estado": ("agotado" if _num(p["cantidad"]) <= 0
                   else "bajo" if _num(p["cantidad"]) <= STOCK_BAJO else "ok"),
    } for p in filas]
    return {"productos": productos, "stock_bajo": STOCK_BAJO}


# ======================================================
# Deudas pendientes
# ======================================================
def deudas_pendientes() -> dict:
    """Mismo cálculo que /deudas de la app: lo que falta según los detalles."""
    with leer() as conn:
        filas = conn.execute(text("""
            SELECT c.id, c.nombre, c.telefono,
                   COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) AS debe,
                   MIN(d.fecha) AS desde
            FROM clientes c
            JOIN deudas d ON d.cliente_id = c.id AND d.estado = 'pendiente'
            JOIN deudas_detalle dd ON dd.deuda_id = d.id AND dd.estado = 'pendiente'
            GROUP BY c.id, c.nombre, c.telefono
            HAVING COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) > :tol
            ORDER BY debe DESC
        """), {"tol": TOLERANCIA}).mappings().all()
    clientes = []
    for c in filas:
        f = _fecha(c["desde"])
        clientes.append({"id": c["id"], "nombre": c["nombre"] or "", "telefono": c["telefono"] or "",
                         "debe": _num(c["debe"]), "desde": f.date().isoformat() if f else ""})
    return {"total": round(sum(c["debe"] for c in clientes), 2), "clientes": clientes}


def deudas_de_cliente(cliente_id: int) -> list:
    with leer() as conn:
        filas = conn.execute(text("""
            SELECT d.id AS deuda_id, d.venta_id, d.fecha, dd.cantidad, dd.precio_unitario,
                   p.nombre AS producto
            FROM deudas d
            JOIN deudas_detalle dd ON dd.deuda_id = d.id AND dd.estado = 'pendiente'
            LEFT JOIN productos p ON p.id = dd.producto_id
            WHERE d.cliente_id = :c AND d.estado = 'pendiente'
            ORDER BY d.fecha DESC, d.id DESC
        """), {"c": cliente_id}).mappings().all()
    salida = []
    for r in filas:
        # el monto sale de la cantidad exacta: tras un pago parcial queda, por
        # ejemplo, 0.6666 x 30 = 20.00, y redondear antes daría 20.10
        monto = round(float(r["cantidad"] or 0) * float(r["precio_unitario"] or 0), 2)
        if monto <= TOLERANCIA:
            continue
        f = _fecha(r["fecha"])
        salida.append({"deuda_id": r["deuda_id"], "venta_id": r["venta_id"],
                       "fecha": f.strftime("%d/%m/%Y") if f else "",
                       "producto": r["producto"] or "Producto",
                       "cantidad": _num(r["cantidad"]), "precio": _num(r["precio_unitario"]),
                       "monto": monto})
    return salida


# ======================================================
# Usuarios (solo para entrar al visor)
# ======================================================
def buscar_usuario(username: str):
    with leer() as conn:
        fila = conn.execute(text(
            "SELECT username, password, rol, activo FROM usuarios WHERE username = :u"),
            {"u": username}).mappings().first()
    return dict(fila) if fila else None
