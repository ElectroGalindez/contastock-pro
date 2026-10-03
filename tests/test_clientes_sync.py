"""
Tests de propagacion de edicion de clientes en toda la app.

Reporte: al editar un cliente, el resto de la app seguia mostrando los datos
viejos. La causa era que /ventas y /clientes copian los clientes al renderizar
(TODOS_CLIENTES en el JS, las filas de la tabla) y no se enteraban de los
cambios hechos en otro modulo o en otra pestana.

Ahora:
  - el guardado de /clientes va por fetch y responde JSON,
  - static/js/clientes-sync.js avisa del cambio a las demas vistas,
  - /ventas parchea TODOS_CLIENTES y repinta el cliente elegido,
  - /ventas/clientes permite recargar la lista desde el servidor,
  - /ventas y /clientes se sirven sin cache para que "atras" no muestre viejos.
"""
import re

import pytest

from app import create_app
from backend.clientes import add_client, get_client


@pytest.fixture
def cliente_sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


def _guardar(cliente_sesion, accion, datos):
    cuerpo = dict(datos)
    cuerpo["action"] = accion
    return cliente_sesion.post("/clientes", data=cuerpo,
                               headers={"X-Requested-With": "fetch"})


# ----------------------------
# Respuesta JSON del guardado
# ----------------------------
def test_editar_devuelve_json_con_los_datos_nuevos(cliente_sesion):
    c = add_client("Nombre Viejo", "0981111", "111", "Dir Vieja", "VIE-1")
    r = _guardar(cliente_sesion, "editar", {
        "cliente_id": c["id"], "nombre": "Nombre Nuevo",
        "telefono": "0982222", "ci": "222", "direccion": "Dir Nueva", "chapa": "NUE-2",
    })
    d = r.get_json()
    assert d["ok"] and d["accion"] == "editar"
    assert d["cliente"]["nombre"] == "Nombre Nuevo"
    assert d["cliente"]["id"] == c["id"]
    assert get_client(c["id"])["nombre"] == "Nombre Nuevo"


def test_crear_devuelve_json_con_el_cliente(cliente_sesion):
    d = _guardar(cliente_sesion, "crear", {
        "nombre": "Cliente Ajax", "telefono": "0999", "ci": "", "direccion": "", "chapa": "",
    }).get_json()
    assert d["ok"] and d["accion"] == "crear"
    assert d["cliente"]["nombre"] == "Cliente Ajax"
    assert d["cliente"]["id"]


def test_error_de_guardado_devuelve_400_y_no_ok(cliente_sesion):
    r = _guardar(cliente_sesion, "crear", {"nombre": "", "telefono": ""})
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_sin_js_sigue_siendo_post_normal(cliente_sesion):
    """Sin la cabecera de fetch no se rompe el flujo con navegador."""
    r = cliente_sesion.post("/clientes", data={
        "action": "crear", "nombre": "Cliente Clasico", "telefono": "", "ci": "",
        "direccion": "", "chapa": "",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"Cliente Clasico" in r.data


# ----------------------------
# Endpoint de recarga del selector
# ----------------------------
def test_ventas_clientes_devuelve_la_lista(cliente_sesion):
    c = add_client("Para El Selector", "0991", None, None, None)
    d = cliente_sesion.get("/ventas/clientes").get_json()
    assert d["clientes"]
    fila = [x for x in d["clientes"] if x["i"] == c["id"]][0]
    assert fila["n"] == "Para El Selector"
    # mismo formato que usa el JS (i, n, t, c, h, d) + banderas de deuda y gestor
    assert set(fila) == {"i", "n", "t", "c", "h", "d", "deb", "g"}
    assert fila["deb"] is False
    assert fila["g"] is False


def test_ventas_clientes_refleja_la_edicion(cliente_sesion):
    """Tras editar, el endpoint ya devuelve el nombre nuevo."""
    c = add_client("Antes Del Cambio", "0992", None, None, None)
    _guardar(cliente_sesion, "editar", {
        "cliente_id": c["id"], "nombre": "Despues Del Cambio",
        "telefono": "0992", "ci": "", "direccion": "", "chapa": "",
    })
    d = cliente_sesion.get("/ventas/clientes").get_json()
    fila = [x for x in d["clientes"] if x["i"] == c["id"]][0]
    assert fila["n"] == "Despues Del Cambio"


# ----------------------------
# El JS de sincronizacion
# ----------------------------
def test_ambas_paginas_cargan_el_modulo_de_sincronizacion(cliente_sesion):
    for ruta in ("/ventas", "/clientes"):
        html = cliente_sesion.get(ruta).get_data(as_text=True)
        assert "js/clientes-sync.js" in html, ruta
        assert "ClientesSync.suscribir" in html or "ClientesSync.publicar" in html, ruta


def test_ventas_se_suscribe_y_parchea_la_lista(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "ClientesSync.suscribir" in html
    assert "recargarClientesDesdeServidor" in html
    assert "/ventas/clientes" in html
    # el cliente ya elegido se repinta con los datos nuevos
    assert "pintarClienteElegido(fresco)" in html
    # y la copia en memoria se actualiza, no se duplica
    assert "TODOS_CLIENTES.findIndex" in html
    assert "TODOS_CLIENTES.splice" in html


def test_clientes_avisa_despues_de_guardar(cliente_sesion):
    html = cliente_sesion.get("/clientes").get_data(as_text=True)
    assert "ClientesSync.publicar" in html
    assert "ClientesSync.recargar" in html
    # actualiza la fila ya pintada sin recargar
    assert "actualizarFilaCliente" in html


def test_no_cache_en_las_paginas_con_datos_de_clientes(cliente_sesion):
    for ruta in ("/ventas", "/clientes", "/ventas_dia"):
        r = cliente_sesion.get(ruta)
        assert "no-store" in r.headers.get("Cache-Control", ""), ruta


def test_el_historico_usa_el_nombre_actual(cliente_sesion):
    """Las ventas guardan cliente_id, no el nombre: el JOIN refleja el cambio.

    Por eso editar un cliente actualiza tambien las ventas ya registradas.
    """
    c = add_client("Nombre En Venta", "0993", None, None, None)
    _guardar(cliente_sesion, "editar", {
        "cliente_id": c["id"], "nombre": "Nombre Corregido",
        "telefono": "0993", "ci": "", "direccion": "", "chapa": "",
    })
    html = cliente_sesion.get("/ventas", query_string={"factura": 0}).get_data(as_text=True)
    # el selector embebido trae el nombre nuevo
    import json
    datos = json.loads(re.search(
        r'<script type="application/json" id="datosClientes">(.*?)</script>',
        html, re.S).group(1))
    fila = [x for x in datos if x["i"] == c["id"]][0]
    assert fila["n"] == "Nombre Corregido"
