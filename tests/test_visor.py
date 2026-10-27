"""
Tests del visor (visor/): el dashboard de solo lectura para el teléfono y la
Mac. Corre sobre la misma base SQLite de los tests, así se comprueba que muestra
los mismos números que la app del almacén.
"""
import importlib.util
import os
import sys
from datetime import date

import bcrypt
import pytest
from sqlalchemy import text

from backend import db, deudas, productos, ventas
from backend.clientes import add_client

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "visor"))
os.environ.setdefault("VISOR_DATABASE_URL", "sqlite://")  # se reemplaza abajo
import datos  # noqa: E402

# visor/app.py se carga con otro nombre: "app" ya es la app del almacén
_spec = importlib.util.spec_from_file_location(
    "visor_app", os.path.join(os.path.dirname(datos.__file__), "app.py"))
visor_app = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(visor_app)


@pytest.fixture(autouse=True)
def visor_sobre_la_base_de_tests():
    datos.usar_engine(db.engine)
    yield
    datos.usar_engine(None)


@pytest.fixture
def venta_con_deuda(producto):
    cli = add_client(nombre="Juan Visor", telefono="555", ci=None, direccion=None, chapa=None)
    v = ventas.register_sale(cli["id"], 30, 10, "admin", "Efectivo",
                             [{"id": producto["id"], "nombre": producto["nombre"],
                               "cantidad": 1, "precio_unitario": 30}])
    deudas.add_debt(cliente_id=cli["id"], monto_total=20, venta_id=v["id"],
                    productos=v["productos_vendidos"], usuario="admin")
    return {"cliente": cli, "venta": v}


def _usuario(username, password, rol="admin", activo=True):
    with db.engine.begin() as conn:
        conn.execute(text("INSERT INTO usuarios (username, password, rol, activo) "
                          "VALUES (:u, :p, :r, :a)"),
                     {"u": username, "p": bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
                      "r": rol, "a": activo})


@pytest.fixture
def cliente_web():
    a = visor_app.create_app()
    a.config.update(TESTING=True, SESSION_COOKIE_SECURE=False)
    return a.test_client()


# ------------------------------------------------------
# Números iguales a la app
# ------------------------------------------------------
def test_resumen_del_dia(venta_con_deuda):
    r = datos.resumen(date.today())
    assert r["ventas_hoy"] == {"cantidad": 1, "total": 30.0, "pagado": 10.0, "pendiente": 20.0}
    assert r["ventas_mes"]["total"] == 30.0
    assert r["serie"][-1] == {"dia": date.today().isoformat(), "total": 30.0}
    assert len(r["serie"]) == 30


def test_deuda_igual_que_en_la_app(venta_con_deuda):
    app_total = round(sum(deudas.deuda_pendiente_por_cliente().values()), 2)
    visor = datos.deudas_pendientes()
    # la app reparte la deuda en cantidades redondeadas (0.67 x 30 = 20.10);
    # lo que importa es que el visor diga exactamente lo mismo
    assert visor["total"] == app_total == pytest.approx(20, abs=0.11)
    assert datos.resumen()["deuda"] == {"total": app_total, "clientes": 1}
    detalle = datos.deudas_de_cliente(venta_con_deuda["cliente"]["id"])
    assert round(sum(d["monto"] for d in detalle), 2) == app_total


def test_pago_parcial_baja_la_deuda(venta_con_deuda, producto):
    d = deudas.debts_by_client(venta_con_deuda["cliente"]["id"])[0]
    antes = datos.deudas_pendientes()["total"]
    deudas.pay_debt_producto(d["deuda_id"], producto["id"], 5, "admin")
    assert datos.deudas_pendientes()["total"] == pytest.approx(antes - 5, abs=0.01)


def test_ventas_del_dia_trae_cliente_y_productos(venta_con_deuda):
    v = datos.ventas_del_dia(date.today())
    assert v["totales"]["cantidad"] == 1
    fila = v["ventas"][0]
    assert fila["cliente"] == "Juan Visor"
    assert fila["saldo"] == 20.0
    assert fila["productos"][0]["cantidad"] == 1


def test_otro_dia_sin_ventas(venta_con_deuda):
    assert datos.ventas_del_dia(date(2000, 1, 1))["ventas"] == []


def test_inventario_marca_stock_bajo(producto):
    productos.update_product(producto["id"], producto["nombre"], 3, producto["precio"])
    inv = datos.inventario()["productos"]
    assert inv[0]["estado"] == "bajo"
    productos.update_product(producto["id"], producto["nombre"], 0, producto["precio"])
    assert datos.inventario()["productos"][0]["estado"] == "agotado"
    assert datos.resumen()["inventario"]["agotados"] == 1


def test_sin_sync_info_no_falla():
    assert datos.resumen()["ultima_subida"] is None


# ------------------------------------------------------
# Acceso
# ------------------------------------------------------
def test_sin_sesion_no_hay_datos(cliente_web):
    assert cliente_web.get("/api/resumen").status_code == 401
    assert cliente_web.get("/").headers["Location"].endswith("/login")


def test_login_admin(cliente_web, venta_con_deuda):
    _usuario("jefe", "clave123")
    r = cliente_web.post("/login", data={"username": "jefe", "password": "clave123"})
    assert r.status_code == 302
    assert cliente_web.get("/api/resumen").get_json()["ventas_hoy"]["total"] == 30.0


def test_empleado_no_entra_por_defecto(cliente_web):
    _usuario("cajero", "clave123", rol="empleado")
    r = cliente_web.post("/login", data={"username": "cajero", "password": "clave123"})
    assert r.status_code == 200
    assert "incorrectos" in r.get_data(as_text=True)


def test_usuario_desactivado_no_entra(cliente_web):
    _usuario("viejo", "clave123", activo=False)
    r = cliente_web.post("/login", data={"username": "viejo", "password": "clave123"})
    assert r.status_code == 200


def test_bloqueo_tras_intentos_fallidos(cliente_web):
    _usuario("jefe", "clave123")
    for _ in range(visor_app.MAX_INTENTOS):
        cliente_web.post("/login", data={"username": "jefe", "password": "mal"})
    r = cliente_web.post("/login", data={"username": "jefe", "password": "clave123"})
    assert "Demasiados intentos" in r.get_data(as_text=True)


def test_visor_no_tiene_rutas_de_escritura(cliente_web):
    app = cliente_web.application
    escrituras = [r.rule for r in app.url_map.iter_rules()
                  if r.methods & {"POST", "PUT", "DELETE", "PATCH"} and r.rule != "/login"]
    assert escrituras == []


def test_resumen_por_producto_del_dia(producto):
    """Tabla de Ventas: cuánto se vendió de cada producto ese día."""
    otro = productos.guardar_producto("Fogón", 10, 50, None, "admin")
    otro_id = [p for p in productos.list_products() if p["nombre"] == "Fogón"][0]["id"]
    cli = add_client(nombre="Ana", telefono="1", ci=None, direccion=None, chapa=None)
    ventas.register_sale(cli["id"], 50, 50, "admin", "Efectivo", [
        {"id": producto["id"], "nombre": producto["nombre"], "cantidad": 2, "precio_unitario": 10},
        {"id": otro_id, "nombre": "Fogón", "cantidad": 3, "precio_unitario": 10}])
    ventas.register_sale(cli["id"], 10, 10, "admin", "Efectivo", [
        {"id": otro_id, "nombre": "Fogón", "cantidad": 1, "precio_unitario": 10}])

    filas = datos.ventas_del_dia(date.today())["por_producto"]
    assert filas[0] == {"nombre": "Fogón", "cantidad": 4.0, "total": 40.0, "ventas": 2}
    assert filas[1] == {"nombre": producto["nombre"], "cantidad": 2.0, "total": 20.0, "ventas": 1}
    assert datos.ventas_del_dia(date(2000, 1, 1))["por_producto"] == []


def test_cada_apertura_lee_neon_al_momento(cliente_web, monkeypatch):
    """Sin memoria intermedia: dos aperturas seguidas = dos lecturas."""
    _usuario("jefe", "clave123")
    cliente_web.post("/login", data={"username": "jefe", "password": "clave123"})
    lecturas = []
    original = datos.inventario
    monkeypatch.setattr(datos, "inventario", lambda: lecturas.append(1) or original())
    cliente_web.get("/api/inventario")
    cliente_web.get("/api/inventario")
    assert len(lecturas) == 2
