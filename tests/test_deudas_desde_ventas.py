"""
Tests de la consulta de deudas desde /ventas.

Pedido: quitar del selector el numero con el total de deudas y dar una opcion
para ver las deudas del cliente. El selector ahora solo muestra un aviso
(icono de exclamacion) cuando el cliente tiene deudas, y el monto se consulta
con el boton "Deudas" / el icono del desplegable.
"""
import re

import pytest

from app import create_app
from backend.clientes import add_client, delete_client
from backend.deudas import add_debt


@pytest.fixture
def cliente_sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


@pytest.fixture
def cliente_con_deuda():
    c = add_client("Cliente Deudor", "0988000", None, None, None)
    add_debt(c["id"], monto_total=1000.0, usuario="test")
    return c


# ----------------------------
# El selector ya no muestra montos
# ----------------------------
def test_el_selector_no_muestra_el_monto_de_deuda(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    cuerpo = re.search(r'function pintarListaClientes.*?\nfunction moverClienteActivo',
                       html, re.S).group(0)
    # el monto se mostraba como "Deuda $..."
    assert "Deuda $" not in cuerpo
    assert "toLocaleString" not in cuerpo
    # en su lugar, un badge de aviso cuando tiene deuda
    assert "bi-exclamation-triangle-fill" in cuerpo
    assert "Deuda</span>" in cuerpo
    assert "Tiene deudas pendientes" in cuerpo


def test_el_selector_ya_no_muestra_al_dia(cliente_sesion):
    """El badge verde 'Al dia' tambien sale: el selector queda limpio."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    cuerpo = re.search(r'function pintarListaClientes.*?\nfunction moverClienteActivo',
                       html, re.S).group(0)
    assert "Al dia" not in cuerpo


def test_el_boton_de_deudas_esta_en_la_ficha(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="btnVerDeudas"' in html
    assert "abrirModalDeudas(" in html
    assert 'id="modalDeudasCliente"' in html


def test_el_desplegable_tiene_el_icono_de_deudas(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "event.stopPropagation();abrirModalDeudas(" in html


# ----------------------------
# Endpoint de deudas
# ----------------------------
def test_endpoint_devuelve_las_deudas(cliente_sesion, cliente_con_deuda):
    c = cliente_con_deuda
    d = cliente_sesion.get(f"/api/clientes/{c['id']}/deudas").get_json()
    assert d["ok"]
    assert d["cliente"]["id"] == c["id"]
    assert d["cliente"]["nombre"] == "Cliente Deudor"
    assert d["total"] == 1
    assert "deuda_total" not in d and "omitidas" not in d
    assert d["deudas"][0]["monto_total"] == 1000.0
    assert d["deudas"][0]["estado"] != "pagada"


def test_endpoint_de_cliente_sin_deudas(cliente_sesion):
    c = add_client("Cliente Sin Deudas", "0988111", None, None, None)
    d = cliente_sesion.get(f"/api/clientes/{c['id']}/deudas").get_json()
    assert d["ok"] and d["deudas"] == []
    assert d["total"] == 0
    assert "omitidas" not in d
    delete_client(c["id"], "limpieza-pruebas")


def test_endpoint_de_cliente_inexistente(cliente_sesion):
    assert cliente_sesion.get("/api/clientes/99999999/deudas").status_code == 404


def test_endpoint_requiere_login():
    app = create_app()
    with app.test_client() as c:
        r = c.get("/api/clientes/1/deudas")
        assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_no_devuelve_las_deudas_pagadas(cliente_sesion, cliente_con_deuda):
    """En /ventas solo interesa lo que falta pagar: las pagadas se omiten."""
    c = cliente_con_deuda
    add_debt(c["id"], monto_total=500.0, estado="pagada", usuario="test")
    d = cliente_sesion.get(f"/api/clientes/{c['id']}/deudas").get_json()
    assert d["total"] == 1                       # solo la pendiente
    assert all(x["estado"] != "pagada" for x in d["deudas"])
    assert [x["monto_total"] for x in d["deudas"]] == [1000.0]


def test_la_descripcion_trae_producto_y_cantidad(cliente_sesion, cliente_con_deuda):
    """La descripcion se arma con el nombre del producto y su cantidad."""
    from backend.productos import guardar_producto
    producto = guardar_producto("Producto Para Deuda", 100.0, 10, None, "test")
    pid = producto["id"] if isinstance(producto, dict) and "id" in producto \
        else producto["id_producto"]

    c = cliente_con_deuda
    nuevo = add_debt(c["id"], productos=[{"id_producto": pid, "cantidad": 3,
                                          "precio_unitario": 100.0}],
                     monto_total=300.0, usuario="test")
    filas = cliente_sesion.get(f"/api/clientes/{c['id']}/deudas").get_json()["deudas"]
    fila = [d for d in filas if d["deuda_id"] == nuevo][0]
    detalle = fila["detalles"][0]
    assert detalle["cantidad"] == 3
    assert detalle["producto"] == "Producto Para Deuda"
    assert detalle["precio_unitario"] == 100.0


# ----------------------------
# El modal
# ----------------------------
def test_la_alerta_solo_para_pendientes_reales(cliente_sesion, cliente_con_deuda):
    """La alerta no puede depender de clientes.deuda_total.

    Esa columna quedo desactualizada y marcaba como deudor a ~500 clientes que
    ya no deben nada. 'deb' se calcula preguntando la tabla deudas.
    """
    from backend.db import engine
    from sqlalchemy import text
    from backend.clientes import clientes_para_selector

    con_deuda = cliente_con_deuda
    sin_deuda = add_client("Cliente Que Ya Pago", "0988222", None, None, None)

    # se simula la columna desactualizada: deuda_total positiva sin deuda real
    with engine.begin() as c:
        c.execute(text("UPDATE clientes SET deuda_total = 5000 WHERE id = :i"),
                  {"i": sin_deuda["id"]})

    datos = {x["i"]: x for x in clientes_para_selector()}
    assert datos[con_deuda["id"]]["deb"] is True       # tiene deuda pendiente
    assert datos[sin_deuda["id"]]["deb"] is False      # no debe nada: sin alerta
    assert datos[sin_deuda["id"]]["d"] == 0.0          # el monto tampoco

    delete_client(sin_deuda["id"], "limpieza-pruebas")


def test_la_alerta_aparece_en_el_cliente_que_debe(cliente_sesion):
    """Ficha del cliente elegido: aviso visible si tiene deudas pendientes."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="alertaDeudaElegido"' in html
    assert "Este cliente tiene deudas pendientes" in html
    assert "bi-exclamation-triangle-fill" in html
    # se muestra u oculta segun la bandera de deuda pendiente, no por el monto
    pintar = re.search(r'function pintarClienteElegido.*?\n}\n', html, re.S).group(0)
    assert "alertaDeudaElegido" in pintar and "!c.deb" in pintar
    assert "formatearMonto" not in pintar
    # el badge del desplegable tambien usa 'deb'
    sel = re.search(r'function pintarListaClientes.*?\nfunction moverClienteActivo',
                    html, re.S).group(0)
    assert "c.deb" in sel
    assert "c.d > 0" not in sel


def test_el_modal_muestra_producto_y_cantidad(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    js = re.search(r'function pintarDeudasCliente.*?\n}\n', html, re.S).group(0)
    # columnas: fecha, producto y cantidad, monto (ya no estado)
    assert "Producto y cantidad" in js
    assert "Deudas pendientes" in js
    # se pidio quitar la metrica de total y el aviso de las pagadas
    assert "Total a pagar" not in js
    assert "deuda ya pagada" not in html and "deudas ya pagadas" not in html
    assert "no tiene deudas pendientes" in html
    # descripcion con producto + cantidad
    desc = re.search(r'function descripcionProductos.*?\n}\n', html, re.S).group(0)
    assert "p.producto" in desc and "p.cantidad" in desc
    assert "unidad" in desc
    # ya no se pinta la columna de estado
    assert ">Estado<" not in js
    # enlace al modulo Deudas
    assert "'/deudas?cliente_id=' + id" in html
