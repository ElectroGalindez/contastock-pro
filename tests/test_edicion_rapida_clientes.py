"""
Tests de la edicion rapida de clientes desde el modulo /ventas.

Pedido: poder editar un cliente rapido y sencillo desde /ventas, sin salir del
modulo. La implementacion:
  - GET  /api/clientes/<id>  -> datos completos (incluye direccion, que la
    copia del navegador no trae) para llenar el modal.
  - PUT  /api/clientes/<id>  -> guarda la edicion.
  - El modalNuevoCliente sirve para crear y para editar.
  - Boton "Editar" en la ficha del cliente elegido, lapiz en cada resultado del
    desplegable, y atajo Ctrl+E.
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


@pytest.fixture
def un_cliente():
    return add_client("Cliente Para Editar", "0987000", "EDT1",
                      "Direccion Original", "EDT-1")


# ----------------------------
# API
# ----------------------------
def test_get_devuelve_el_cliente_completo(cliente_sesion, un_cliente):
    d = cliente_sesion.get(f"/api/clientes/{un_cliente['id']}").get_json()
    c = d["cliente"]
    assert d["ok"] and c["id"] == un_cliente["id"]
    # la direccion viene del servidor: el selector del navegador no la trae
    assert c["direccion"] == "Direccion Original"
    assert c["nombre"] == "Cliente Para Editar"
    assert c["ci"] == "EDT1" and c["chapa"] == "EDT-1"


def test_get_de_cliente_inexistente(cliente_sesion):
    assert cliente_sesion.get("/api/clientes/99999999").status_code == 404


def test_put_actualiza_el_cliente(cliente_sesion, un_cliente):
    d = cliente_sesion.put(f"/api/clientes/{un_cliente['id']}", json={
        "nombre": "Editado En Ventas", "telefono": "0987111", "ci": "EDT2",
        "chapa": "EDT-2", "direccion": "Direccion Nueva",
    }).get_json()
    assert d["ok"] and d["cliente"]["nombre"] == "Editado En Ventas"
    bd = get_client(un_cliente["id"])
    assert bd["nombre"] == "Editado En Ventas"
    assert bd["ci"] == "EDT2" and bd["direccion"] == "Direccion Nueva"


def test_put_tambien_acepta_post(cliente_sesion, un_cliente):
    r = cliente_sesion.post(f"/api/clientes/{un_cliente['id']}",
                            json={"nombre": "Vía POST", "telefono": "", "ci": "",
                                  "chapa": "", "direccion": ""})
    assert r.get_json()["ok"]


def test_put_exige_nombre(cliente_sesion, un_cliente):
    r = cliente_sesion.put(f"/api/clientes/{un_cliente['id']}", json={"nombre": "   "})
    assert r.status_code == 400
    assert r.get_json()["error"]


def test_put_de_cliente_inexistente(cliente_sesion):
    r = cliente_sesion.put("/api/clientes/99999999", json={"nombre": "X"})
    assert r.status_code == 404


def test_la_edicion_se_refleja_en_el_selector(cliente_sesion, un_cliente):
    cliente_sesion.put(f"/api/clientes/{un_cliente['id']}", json={
        "nombre": "Nombre Del Selector", "telefono": "0987222", "ci": "EDT3",
        "chapa": "EDT-3", "direccion": ""})
    sel = {x["i"]: x for x in cliente_sesion.get("/ventas/clientes").get_json()["clientes"]}
    fila = sel[un_cliente["id"]]
    assert fila["n"] == "Nombre Del Selector"
    assert fila["c"] == "EDT3" and fila["h"] == "EDT-3"


def test_la_edicion_no_crea_clientes_nuevos(cliente_sesion, un_cliente):
    """Editar no debe duplicar el cliente en la lista del selector."""
    antes = len(cliente_sesion.get("/ventas/clientes").get_json()["clientes"])
    cliente_sesion.put(f"/api/clientes/{un_cliente['id']}", json={
        "nombre": "Sin Duplicar", "telefono": "", "ci": "", "chapa": "", "direccion": ""})
    d = cliente_sesion.get("/ventas/clientes").get_json()["clientes"]
    assert len(d) == antes
    assert len([x for x in d if x["i"] == un_cliente["id"]]) == 1


# ----------------------------
# Interfaz
# ----------------------------
def test_boton_editar_en_la_ficha_del_elegido(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="btnEditarCliente"' in html
    assert "editarClienteSeleccionado" in html
    # el boton de crear sigue existiendo
    assert 'data-bs-target="#modalNuevoCliente"' in html


def test_lapiz_en_cada_resultado_del_desplegable(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "event.stopPropagation();abrirModalEditarCliente(" in html
    # el item paso a div para poder anadir un boton dentro (HTML valido)
    assert "document.createElement('div')" in html
    assert "event.stopPropagation()" in html


def test_atajo_de_teclado_ctrl_e(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "e.key.toLowerCase() !== 'e'" in html
    assert "editarClienteSeleccionado();" in html


def test_modal_sirve_para_crear_y_editar(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="nuevoCliId"' in html          # marca el modo edicion
    assert 'id="modalCliTitulo"' in html
    assert "guardarClienteModal" in html
    # decide crear o editar segun haya id
    assert "editando ? '/api/clientes/' + id : '/api/clientes'" in html
    assert "editando ? 'PUT' : 'POST'" in html
    # y el guardado viejo quedo sustituido
    assert "function guardarNuevoCliente" not in html


def test_el_modal_se_reinicia_al_cerrarse(cliente_sesion):
    """Si no, el boton 'Nuevo Cliente' abriria en modo edicion con un id viejo."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "'hidden.bs.modal'" in html
    assert "document.getElementById('nuevoCliId').value = ''" in html


def test_editar_reinicia_el_id_abriendo_nuevo(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    cuerpo = re.search(r'function abrirModalNuevoClienteCon.*?\n}', html, re.S).group(0)
    assert "nuevoCliId" in cuerpo


def test_tras_editar_se_avisa_a_las_otras_vistas(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "ClientesSync.publicar(editando ? 'editar' : 'crear', c)" in html
    assert "ClientesSync.recargar()" in html
    # el cliente elegido se repinta y la lista abierta se vuelve a pintar
    assert "pintarClienteElegido(ficha)" in html
    assert "pintarListaClientes();" in html
