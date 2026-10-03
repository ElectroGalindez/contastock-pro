"""El estado de las deudas: al pagarla tiene que quedar en "pagada".

Cubre los tres pedidos del usuario sobre deudas:
  - la venta se ordena por id, en el orden en que se hizo;
  - pagar la deuda la deja en "pagada";
  - el selector del modulo de deudas trabaja por id como el de /clientes.
"""
import re
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

from app import create_app
from backend.clientes import add_client
from backend.categorias import agregar_categoria, list_categories
from backend.productos import guardar_producto
from backend.db import engine


@pytest.fixture
def sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


@pytest.fixture
def producto():
    agregar_categoria("Cat Deuda")
    cat = [c["id"] for c in list_categories() if c["nombre"] == "Cat Deuda"][0]
    return guardar_producto("Producto Deuda", 100.0, 50, cat, "test")


def _pagar(sesion, deuda_id, producto_id, monto):
    return sesion.post("/deudas", data={
        "action": "pagar", "deuda_id": deuda_id, "producto_id": producto_id,
        "monto_pago": monto, "metodo_pago": "Efectivo",
    }, follow_redirects=True)


def _items(producto, total):
    return ('[{"id_producto": %s, "nombre": "Producto Deuda",'
            ' "cantidad": 1, "precio_unitario": %s}]' % (producto["id"], total))


def _venta_con_deuda(sesion, producto, cliente_id, total, pagado):
    """Registra una venta a credito. Devuelve (venta_id, deuda_id)."""
    r = sesion.post("/ventas", data={
        "action": "registrar", "cliente_id": cliente_id,
        "items": _items(producto, total),
        "total": total, "pagado": pagado, "tipo_pago": "Efectivo",
    }, follow_redirects=True)
    assert r.status_code == 200
    with engine.connect() as c:
        venta_id = c.execute(text("SELECT MAX(id) FROM ventas")).scalar()
        deuda_id = c.execute(text("SELECT id FROM deudas WHERE venta_id = :v"),
                             {"v": venta_id}).scalar()
    return venta_id, deuda_id


# ----------------------------
# Pagar la deuda la deja en pagada
# ----------------------------
def test_pagar_toda_la_deuda_la_deja_pagada(sesion, producto):
    """Caso del usuario: se paga el saldo completo y la deuda queda pagada."""
    from backend.deudas import get_debt

    cliente = add_client("Deudor UNO", "0991", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, cliente["id"], 500, 200)  # saldo 300

    with engine.connect() as c:
        estado = c.execute(text("SELECT estado FROM deudas WHERE id = :i"),
                           {"i": deuda_id}).scalar()
    assert estado == "pendiente"

    r = _pagar(sesion, deuda_id, producto["id"], 300)
    assert r.status_code == 200

    with engine.connect() as c:
        estado = c.execute(text("SELECT estado FROM deudas WHERE id = :i"),
                           {"i": deuda_id}).scalar()
    assert estado == "pagada", "la deuda quedo pendiente despues de pagarla entera"
    assert get_debt(deuda_id)["estado"] == "pagada"


def test_pagar_a_medias_la_deja_pendiente(sesion, producto):
    from backend.deudas import get_debt

    cliente = add_client("Deudor DOS", "0992", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, cliente["id"], 500, 0)   # saldo 500

    _pagar(sesion, deuda_id, producto["id"], 200)

    with engine.connect() as c:
        estado = c.execute(text("SELECT estado FROM deudas WHERE id = :i"),
                           {"i": deuda_id}).scalar()
    assert estado == "pendiente"
    assert get_debt(deuda_id)["estado"] == "pendiente"


def test_los_detalles_suman_lo_mismo_que_la_deuda(sesion, producto):
    """La causa del bug: los detalles valian el total de la venta, no el saldo.

    Con una venta de 500 y 200 pagados, la deuda es 300 pero sus productos
    sumaban 500, asi que al pagar 300 sobraban 200 y nunca pasaba a pagada.
    """
    cliente = add_client("Deudor TRES", "0993", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, cliente["id"], 500, 200)

    with engine.connect() as c:
        total = c.execute(text("SELECT monto_total FROM deudas WHERE id = :i"),
                          {"i": deuda_id}).scalar()
        detalle = c.execute(text(
            "SELECT SUM(cantidad * precio_unitario) FROM deudas_detalle "
            "WHERE deuda_id = :i"), {"i": deuda_id}).scalar()
    assert float(detalle) == float(total) == 300.0


def test_una_deuda_paga_no_vuelve_a_pendiente(sesion, producto):
    """Volver a pagar algo sobre una deuda ya saldada no la reabre."""
    cliente = add_client("Deudor CUATRO", "0994", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, cliente["id"], 300, 0)

    _pagar(sesion, deuda_id, producto["id"], 300)
    _pagar(sesion, deuda_id, producto["id"], 50)

    with engine.connect() as c:
        estado = c.execute(text("SELECT estado FROM deudas WHERE id = :i"),
                           {"i": deuda_id}).scalar()
    assert estado == "pagada"


def test_el_cliente_deja_de_figurar_con_deuda(sesion, producto):
    """Si ya esta saldado, no tiene que seguir apareciendo en el selector."""
    cliente = add_client("Deudor CINCO", "0995", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, cliente["id"], 300, 0)
    _pagar(sesion, deuda_id, producto["id"], 300)

    from backend.deudas import list_clientes_con_deuda
    assert cliente["id"] not in [c["id"] for c in list_clientes_con_deuda()]


def test_un_cliente_con_deuda_aparece_aunque_deuda_total_diga_cero(sesion, producto):
    """No se puede filtrar por clientes.deuda_total: esa columna miente.

    Es la razon por la que el selector no mostraba todos los deudores.
    """
    cliente = add_client("Deudor SEIS", "0996", None, None, None)
    _venta_con_deuda(sesion, producto, cliente["id"], 400, 0)

    with engine.begin() as c:      # la columna quedo en cero, como pasa en datos viejos
        c.execute(text("UPDATE clientes SET deuda_total = 0 WHERE id = :i"),
                  {"i": cliente["id"]})

    from backend.deudas import list_clientes_con_deuda
    encontrado = [c for c in list_clientes_con_deuda() if c["id"] == cliente["id"]]
    assert encontrado, "el cliente con deuda pendiente no aparece en el selector"
    assert float(encontrado[0]["deuda_total"]) == 400.0


# ----------------------------
# Orden por id
# ----------------------------
def test_las_ventas_del_gestor_salen_en_orden_por_id(sesion, producto):
    """En el orden en que se hicieron, no por fecha: la fecha se puede editar."""
    from backend.clientes import set_gestor
    from backend.ventas import ventas_de_gestor, resumen_por_gestor

    gestor = add_client("Gestor Orden", "0997", None, None, None)
    set_gestor(gestor["id"], True, "test")

    for total in (100, 200, 300):
        sesion.post("/ventas", data={
            "action": "registrar", "cliente_id": gestor["id"],
            "items": _items(producto, total),
            "total": total, "pagado": total, "tipo_pago": "Efectivo",
            "gestor_id": gestor["id"],
        }, follow_redirects=True)
    with engine.connect() as c:
        ids = [r[0] for r in c.execute(text(
            "SELECT id FROM ventas WHERE gestor_id = :g ORDER BY id"),
            {"g": gestor["id"]}).all()]

    # se desordena la fecha a proposito: el id es el que manda
    with engine.begin() as c:
        c.execute(text("UPDATE ventas SET fecha = :f WHERE id = :i"),
                  {"f": datetime(2020, 1, 1), "i": ids[0]})

    ventas = ventas_de_gestor(gestor["id"],
                              fecha_inicio=date(2019, 1, 1),
                              fecha_fin=date(2030, 1, 1))
    assert [v["id"] for v in ventas] == ids, "no respeta el orden en que se hicieron"


def test_el_resumen_also_acepta_el_rango(sesion, producto):
    from backend.ventas import resumen_por_gestor

    gestor = add_client("Gestor Rango", "0998", None, None, None)
    for total in (100, 900):
        sesion.post("/ventas", data={
            "action": "registrar", "cliente_id": gestor["id"],
            "items": _items(producto, total),
            "total": total, "pagado": total, "tipo_pago": "Efectivo",
            "gestor_id": gestor["id"],
        }, follow_redirects=True)

    con_rango = resumen_por_gestor(date(2019, 1, 1), date(2030, 1, 1))
    assert con_rango[gestor["id"]]["ventas"] == 2
    sin_rango = resumen_por_gestor()
    assert sin_rango[gestor["id"]]["ventas"] == 2


# ----------------------------
# Selector de /deudas: busca por id como /clientes
# ----------------------------
def test_el_selector_busca_por_id_entre_todos_los_clientes(sesion):
    """Un cliente sin deuda tiene que aparecer al buscarlo por ID.

    Antes el selector solo traia clientes con deuda, asi que un cliente al dia
    no se podia seleccionar desde /deudas.
    """
    limpio = add_client("Cliente Sin Deuda", "0900", None, None, None)
    r = sesion.get("/deudas/buscar?q=" + str(limpio["id"]))
    assert r.status_code == 200
    filas = r.get_json()["rows"]
    assert limpio["id"] in [f["id"] for f in filas], "no se encuentra por ID"


def test_el_selector_trae_el_importe_real_de_cada_cliente(sesion, producto):
    deudor = add_client("Cliente Con Deuda", "0901", None, None, None)
    _venta_con_deuda(sesion, producto, deudor["id"], 400, 100)   # saldo 300

    r = sesion.get("/deudas/buscar?q=" + str(deudor["id"]))
    fila = [f for f in r.get_json()["rows"] if f["id"] == deudor["id"]][0]
    assert fila["deuda"] == 300.0


def test_el_id_exacto_va_primero(sesion):
    """Buscar un numero tiene que traer a ese cliente arriba, no al que empieza con el."""
    add_client("Ana 2000", "0902", None, None, None)
    objetivo = add_client("Zulma 1", "0903", None, None, None)
    r = sesion.get("/deudas/buscar?q=" + str(objetivo["id"]))
    assert r.get_json()["rows"][0]["id"] == objetivo["id"]


def test_la_busqueda_ignora_acentos(sesion):
    """Como en /clientes: escribir sin tilde tiene que encontrar al cliente."""
    con_tilde = add_client("Rodríguez", "0904", None, None, None)
    r = sesion.get("/deudas/buscar?q=rodriguez")
    assert con_tilde["id"] in [f["id"] for f in r.get_json()["rows"]]


def test_sin_termino_devuelve_lista_vacia(sesion):
    r = sesion.get("/deudas/buscar?q=")
    assert r.get_json() == {"rows": [], "total": 0, "mostrando": 0}


def test_el_buscador_de_deudas_no_devuelve_pii_de_mas(sesion):
    """El selector no necesita la direccion ni el rol, solo lo del buscador."""
    c = add_client("Cliente PII", "0905", None, None, None)
    r = sesion.get("/deudas/buscar?q=" + str(c["id"]))
    fila = r.get_json()["rows"][0]
    assert set(fila) == {"id", "nombre", "telefono", "ci", "chapa", "deuda"}


def test_la_pagina_de_deudas_no_se_cachea(sesion):
    """Si se cachea, el boton atras muestra deudores que ya se pagaron."""
    r = sesion.get("/deudas")
    assert "no-store" in r.headers.get("Cache-Control", "")


def test_el_saldo_del_cliente_no_queda_con_restos_de_division(sesion, producto):
    """Un pago parcial deja 399.99999999999994 si no se redondea a centavos."""
    deudor = add_client("Cliente Saldo", "0906", None, None, None)
    _, deuda_id = _venta_con_deuda(sesion, producto, deudor["id"], 500, 100)  # 400
    _pagar(sesion, deuda_id, producto["id"], 1)                            # queda 399

    from backend.clientes import get_client
    assert get_client(deudor["id"])["deuda_total"] == 399.0


def test_la_api_del_panel_devuelve_las_ventas_en_orden(sesion, producto):
    """Es lo que el usuario ve al desplegar un gestor en el panel."""
    from backend.clientes import set_gestor

    gestor = add_client("Gestor Panel", "0907", None, None, None)
    set_gestor(gestor["id"], True, "test")
    for total in (10, 20, 30):
        sesion.post("/ventas", data={
            "action": "registrar", "cliente_id": gestor["id"],
            "items": _items(producto, total),
            "total": total, "pagado": total, "tipo_pago": "Efectivo",
            "gestor_id": gestor["id"],
        }, follow_redirects=True)

    r = sesion.get("/api/gestores/%d/ventas?todo=1" % gestor["id"])
    ids = [v["id"] for v in r.get_json()["ventas"]]
    assert ids == sorted(ids), "el panel no las muestra en el orden en que se hicieron"
