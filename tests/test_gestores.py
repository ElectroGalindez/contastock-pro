"""Gestores: clientes marcados para cobrar comisión.

Un gestor NO es una entidad nueva: sigue siendo un cliente de la tabla clientes
con una bandera es_gestor. Estos tests fijan justo eso, para que nadie lo
convierta en tabla aparte ni saque a los gestores de /clientes o /ventas.
"""
import re
from datetime import date, datetime, timedelta

import pytest


@pytest.fixture
def cliente_sesion():
    from app import create_app
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


def _cat():
    from backend.categorias import agregar_categoria, list_categories
    # idempotente: algunos tests registran varias ventas y cada llamada a
    # agregar_categoria con el mismo nombre tiraba "ya existe".
    if "Cat Gestor" in [c["nombre"] for c in list_categories()]:
        return [c["id"] for c in list_categories() if c["nombre"] == "Cat Gestor"][0]
    agregar_categoria("Cat Gestor")
    return list_categories()[-1]["id"]


@pytest.fixture
def tres_clientes():
    from backend.clientes import add_client, delete_client
    nuevos = [
        add_client("Gestor Uno", "0991000001", None, None, None),
        add_client("Gestor Dos", "0991000002", None, None, None),
        add_client("Cliente Comun", "0991000003", None, None, None),
    ]
    yield nuevos
    for n in nuevos:
        delete_client(n["id"], "limpieza-pruebas")


# ----------------------------
# La bandera
# ----------------------------
def test_marcar_gestor_no_lo_saca_de_clientes(tres_clientes):
    """Marcarlo gestor no crea otra entidad: sigue en la lista de clientes."""
    from backend.clientes import set_gestor, list_clients, listar_gestores

    objetivo = tres_clientes[0]
    set_gestor(objetivo["id"], True, "test")

    assert objetivo["id"] in [c["id"] for c in list_clients()]
    assert objetivo["id"] in [c["id"] for c in listar_gestores()]

    # el resto de los clientes no se ven afectados
    assert tres_clientes[1]["id"] not in [c["id"] for c in listar_gestores()]


def test_gestor_sigue_pudiendo_endeudarse(tres_clientes):
    """Un gestor puede comprar y deber igual que cualquier cliente."""
    from backend.clientes import set_gestor
    from backend.deudas import add_debt, list_debts

    objetivo = tres_clientes[0]
    set_gestor(objetivo["id"], True, "test")
    add_debt(cliente_id=objetivo["id"], monto_total=500.0, usuario="test")

    debts = [d for d in list_debts() if d["cliente_id"] == objetivo["id"]]
    assert sum(d["monto_total"] for d in debts) == 500.0


def test_desmarcar_no_borra_al_cliente(tres_clientes):
    from backend.clientes import set_gestor, get_client, listar_gestores

    objetivo = tres_clientes[0]
    set_gestor(objetivo["id"], True, "test")
    set_gestor(objetivo["id"], False, "test")

    assert get_client(objetivo["id"]) is not None
    assert objetivo["id"] not in [c["id"] for c in listar_gestores()]


def test_acepta_formas_de_booleano(tres_clientes):
    from backend.clientes import set_gestor

    for valor in (True, 1, "1", "si", "sí", "on"):
        assert set_gestor(tres_clientes[0]["id"], valor, "test")["es_gestor"] == 1
    for valor in (False, 0, "0", "no", "off", ""):
        assert set_gestor(tres_clientes[0]["id"], valor, "test")["es_gestor"] == 0


def test_marcar_varios_de_a_una_vez(tres_clientes):
    from backend.clientes import set_gestores_masivo, listar_gestores

    ids = [tres_clientes[0]["id"], tres_clientes[1]["id"]]
    assert set_gestores_masivo(ids, True, "test") == 2

    gestores = {c["id"] for c in listar_gestores()}
    assert set(ids).issubset(gestores)
    assert tres_clientes[2]["id"] not in gestores

    assert set_gestores_masivo(ids, False, "test") == 2
    gestores = {c["id"] for c in listar_gestores()}
    assert not set(ids) & gestores


def test_marcar_lista_vacia_no_rompe_nada(tres_clientes):
    from backend.clientes import set_gestores_masivo, count_gestores

    antes = count_gestores()
    assert set_gestores_masivo([], True, "test") == 0
    assert count_gestores() == antes


def test_un_id_suelto_no_marca_otros_clientes(tres_clientes):
    """Un id en texto no debe recorrerse caracter por caracter.

    "218" como texto, con un for encima, marcaba los clientes 2, 1 y 8.
    """
    from backend.clientes import set_gestores_masivo, listar_gestores
    from sqlalchemy import text
    from backend.db import engine

    # Se preparan los tres ids de un digito de "218" (1, 2 y 8) junto al de dos
    # digitos. Los 1 y 2 ya existen por tres_clientes; el 8 se inserta aca para
    # que el caso no dependa de los ids que toque en cada corrida.
    with engine.begin() as c:
        c.execute(text("UPDATE clientes SET es_gestor = 1 WHERE id IN (1, 2)"))
        c.execute(text("INSERT INTO clientes (id, nombre, es_gestor) "
                       "VALUES (8, 'Suelto 8', 1)"))
        c.execute(text("INSERT INTO clientes (id, nombre, es_gestor) "
                       "VALUES (218, 'Suelto 218', 1)"))

    assert set_gestores_masivo("218", False, "test") == 1

    marcados = {c_["id"] for c_ in listar_gestores()}
    # se desmarco solo el 218: 1, 2 y 8 quedaron intactos
    assert 218 not in marcados
    assert {1, 2, 8} <= marcados


def test_descarta_ids_invalidos(tres_clientes):
    from backend.clientes import set_gestores_masivo, listar_gestores

    assert set_gestores_masivo(["abc", None, ""], True, "test") == 0
    assert listar_gestores() == [] or all(g["id"] in
                                           {t["id"] for t in tres_clientes}
                                           for g in listar_gestores())


# ----------------------------
# El panel
# ----------------------------
def test_el_panel_lista_los_gestores(cliente_sesion, tres_clientes):
    from backend.clientes import set_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")

    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    assert gestor["nombre"] in html
    assert f'id="gestor{gestor["id"]}"' in html
    # los otros clientes tambien aparecen, con el switch apagado
    assert tres_clientes[2]["nombre"] in html
    assert f'id="gestor{tres_clientes[2]["id"]}"' in html
    # aviso de que sigue siendo cliente
    assert "Un gestor sigue siendo un cliente" in html


def test_filtrar_solo_gestores(cliente_sesion, tres_clientes):
    from backend.clientes import set_gestor

    set_gestor(tres_clientes[0]["id"], True, "test")

    html = cliente_sesion.get("/gestores?vista=gestores").get_data(as_text=True)
    assert tres_clientes[0]["nombre"] in html
    assert tres_clientes[2]["nombre"] not in html


def test_el_panel_marca_con_fetch(cliente_sesion, tres_clientes):
    objetivo = tres_clientes[0]
    r = cliente_sesion.post(
        "/api/gestores",
        json={"cliente_id": objetivo["id"], "es_gestor": True},
        headers={"X-Requested-With": "fetch"},
    )
    assert r.get_json()["ok"] is True
    assert r.get_json()["gestor"] if "gestor" in r.get_json() else True

    from backend.clientes import listar_gestores
    assert objetivo["id"] in [c["id"] for c in listar_gestores()]


def test_el_panel_marca_varios_con_fetch(cliente_sesion, tres_clientes):
    ids = [tres_clientes[0]["id"], tres_clientes[1]["id"]]
    r = cliente_sesion.post(
        "/api/gestores",
        json={"cliente_ids": ids, "es_gestor": True},
        headers={"X-Requested-With": "fetch"},
    )
    j = r.get_json()
    assert j["ok"] is True and j["actualizados"] == 2

    from backend.clientes import listar_gestores
    assert set(ids).issubset({c["id"] for c in listar_gestores()})


def test_el_menu_tiene_el_item_de_gestores(cliente_sesion):
    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    assert re.search(r'href="/gestores"[\s\S]{0,160}?Gestores', html)


def test_tabla_de_clientes_muestra_la_etiqueta(cliente_sesion, tres_clientes):
    from backend.clientes import set_gestor

    set_gestor(tres_clientes[0]["id"], True, "test")
    html = cliente_sesion.get("/clientes").get_data(as_text=True)
    fila = re.search(r'<tr data-nombre="[^"]*" data-id="%d">.*?</tr>' % tres_clientes[0]["id"],
                     html, re.S)
    assert fila is not None
    assert "Gestor" in fila.group(0)
    assert "bi-person-badge-fill" in fila.group(0)


# ----------------------------
# El selector de /ventas
# ----------------------------
def test_ventas_tiene_selector_de_gestor(cliente_sesion, tres_clientes):
    from backend.clientes import set_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")

    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="gestorId"' in html
    # la opcion vacia: la venta puede venir sin gestor
    assert '<option value="">Sin gestor</option>' in html
    assert '<option value="%d">' % gestor["id"] in html
    # solo los gestores aparecen en el desplegable, no todos los clientes
    assert '<option value="%d">' % tres_clientes[2]["id"] not in html


def test_el_cliente_mantiene_la_marca_gestor(tres_clientes):
    """El selector de cliente lleva 'g' para pintar la etiqueta."""
    from backend.clientes import set_gestor, clientes_para_selector
    from sqlalchemy import text
    from backend.db import engine

    objetivo = tres_clientes[0]
    with engine.begin() as c:
        c.execute(text("UPDATE clientes SET es_gestor = 1 WHERE id = :i"),
                  {"i": objetivo["id"]})
    set_gestor(objetivo["id"], True, "test")

    datos = {c["i"]: c for c in clientes_para_selector()}
    assert datos[objetivo["id"]]["g"] is True
    assert datos[tres_clientes[2]["id"]]["g"] is False


def test_la_etiqueta_gestor_aparece_en_el_selector(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    sel = re.search(r"function pintarListaClientes.*?\nfunction moverClienteActivo", html, re.S)
    assert "c.g" in sel.group(0)
    assert "Gestor" in sel.group(0)
    # tambien en la ficha del cliente elegido
    assert "elegidoGestorBadge" in html
    assert "!c.g" in html


def test_ventas_avisa_si_no_hay_gestores(cliente_sesion, tres_clientes):
    from backend.clientes import listar_gestores
    from sqlalchemy import text
    from backend.db import engine

    with engine.begin() as c:
        c.execute(text("UPDATE clientes SET es_gestor = 0"))

    assert listar_gestores() == []
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "Todavia no hay gestores marcados" in html
    assert 'href="/gestores"' in html


def test_el_panel_filtra_sin_viajar_al_servidor(cliente_sesion, tres_clientes):
    """Con ~2000 clientes el buscador filtra en el navegador, no recarga."""
    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    assert "filtrarGestores" in html
    # cada fila lleva el texto buscable y el filtro no navega
    assert html.count("data-buscar=") >= 3
    assert "window.location" not in html
    # el toggle "seleccionar todos" respeta lo que se ve
    sel = re.search(r"function seleccionarTodos.*?\n}\n", html, re.S).group(0)
    assert "style.display !== 'none'" in sel


def test_el_buscador_sigue_aceptando_la_url(cliente_sesion, tres_clientes):
    """Sin JS, el buscador del panel tambien funciona por query string."""
    html = cliente_sesion.get("/gestores?q=Gestor%20Dos").get_data(as_text=True)
    assert tres_clientes[1]["nombre"] in html
    assert tres_clientes[2]["nombre"] not in html


# ----------------------------
# La venta muestra el gestor
# ----------------------------
def _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=True):
    """Registra una venta de un cliente y devuelve la fila del historial."""
    from backend.clientes import set_gestor
    from backend.productos import guardar_producto

    gestor = tres_clientes[0]
    if con_gestor:
        set_gestor(gestor["id"], True, "test")
    prod = guardar_producto("Producto Historial", 10.0, 5, _cat(), "test")

    data = {
        "action": "registrar",
        "cliente_id": tres_clientes[1]["id"],
        "items": '[{"id_producto": %s, "nombre": "Producto Historial",'
                 ' "cantidad": 1, "precio_unitario": 10}]' % prod["id"],
        "total": 10, "pagado": 10, "tipo_pago": "Efectivo",
    }
    if con_gestor:
        data["gestor_id"] = gestor["id"]
    assert cliente_sesion.post("/ventas", data=data).status_code in (200, 302)

    from backend.ventas import ultima_venta
    return ultima_venta()


def test_el_historial_muestra_quien_trajo_la_venta(cliente_sesion, tres_clientes):
    """Sin esto el gestor queda guardado y no se sabe nunca a quien se le paga."""
    venta = _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=True)

    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    fila = re.search(r'<tr>\s*<td class="text-muted">#%d</td>.*?</tr>' % venta["id"],
                     html, re.S)
    assert fila is not None
    assert tres_clientes[0]["nombre"] in fila.group(0)
    assert "bi-person-badge-fill" in fila.group(0)


def test_una_venta_sin_gestor_no_muestra_etiqueta(cliente_sesion, tres_clientes):
    venta = _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=False)

    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    fila = re.search(r'<tr>\s*<td class="text-muted">#%d</td>.*?</tr>' % venta["id"],
                     html, re.S)
    assert fila is not None
    assert "bi-person-badge-fill" not in fila.group(0)


def test_la_columna_gestor_ocupa_una_celda(cliente_sesion, tres_clientes):
    """El encabezado y el colspan del detalle tienen que llevar la columna."""
    _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=True)

    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert re.search(r"<th>Gestor</th>", html)
    # 8 columnas + la nueva: el detalle no puede quedar desalineado
    assert 'colspan="9"' in html


def test_la_busqueda_devuelve_el_gestor(cliente_sesion, tres_clientes):
    """El buscador de ventas tambien lo trae, si no disappears al filtrar."""
    venta = _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=True)

    r = cliente_sesion.get("/ventas/buscar?q=%d" % venta["id"]).get_json()
    fila = [x for x in r["rows"] if x["id"] == venta["id"]][0]
    assert fila["gestor"] == tres_clientes[0]["nombre"]


def test_la_busqueda_sin_gestor_devuelve_vacio(cliente_sesion, tres_clientes):
    venta = _venta_con_gestor(cliente_sesion, tres_clientes, con_gestor=False)

    r = cliente_sesion.get("/ventas/buscar?q=%d" % venta["id"]).get_json()
    fila = [x for x in r["rows"] if x["id"] == venta["id"]][0]
    assert fila["gestor"] == ""


def test_el_aviso_no_interpreta_el_nombre_como_html(cliente_sesion, tres_clientes):
    """El nombre del cliente llega al aviso: tiene que ir como texto, no como HTML.

    Jinja escapa el atributo data-nombre, pero avisar() lo volcaria con innerHTML
    y un cliente llamado "<img src=x onerror=...>" se ejecutaria en el navegador
    de quien usa la app.
    """
    from backend.clientes import add_client

    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    aviso = re.search(r"function avisar.*?\n}\n", html, re.S).group(0)
    assert "textContent = texto" in aviso
    assert "innerHTML = '<div class='alert" not in aviso

    # y el nombre malicioso llega escapado al atributo, no crudo
    malicioso = add_client('<img src=x onerror=alert(1)>', "0991000009", None, None, None)
    try:
        html = cliente_sesion.get("/gestores").get_data(as_text=True)
        assert "onerror=alert(1)>" not in html
        assert "&lt;img src=x onerror=alert(1)&gt;" in html
    finally:
        from backend.clientes import delete_client
        delete_client(malicioso["id"], "limpieza-pruebas")


# ----------------------------
# El panel muestra las ventas del gestor
# ----------------------------
def _venta(cliente_sesion, comprador_id, gestor_id, total, pagado):
    from backend.productos import guardar_producto
    from backend.ventas import ultima_venta

    prod = guardar_producto("Prod Panel Gestor", 10.0, 50, _cat(), "test")
    data = {
        "action": "registrar",
        "cliente_id": comprador_id,
        "items": '[{"id_producto": %s, "nombre": "Prod Panel Gestor",'
                 ' "cantidad": 1, "precio_unitario": %s}]' % (prod["id"], total),
        "total": total, "pagado": pagado, "tipo_pago": "Efectivo",
    }
    if gestor_id is not None:
        data["gestor_id"] = gestor_id
    assert cliente_sesion.post("/ventas", data=data).status_code in (200, 302)
    return ultima_venta()


def test_el_panel_muestra_las_ventas_del_gestor(cliente_sesion, tres_clientes):
    """Al filtrar un gestor, el panel tiene que dar sus ventas."""
    from backend.clientes import set_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 500, 500)

    html = cliente_sesion.get("/gestores?q=Gestor+Uno").get_data(as_text=True)
    # la fila del gestor tiene su boton y su fila de ventas
    assert 'id="btnVentas%d"' % gestor["id"] in html
    assert 'id="ventasGestor%d"' % gestor["id"] in html
    assert "toggleVentasGestor(%d)" % gestor["id"] in html
    assert "/api/gestores/' + id + '/ventas" in html


def test_el_endpoint_devuelve_las_ventas_asociadas(cliente_sesion, tres_clientes):
    """Cada venta devuelta es del gestor pedido, y trae su cliente y estado."""
    gestor = tres_clientes[0]
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 500, 500)
    _venta(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 300, 100)

    j = cliente_sesion.get("/api/gestores/%d/ventas" % gestor["id"]).get_json()
    assert j["ok"] is True
    assert j["gestor"]["nombre"] == gestor["nombre"]
    assert len(j["ventas"]) == 2

    por_id = {v["id"]: v for v in j["ventas"]}
    pagada = [v for v in j["ventas"] if v["total"] == 500][0]
    pendiente = [v for v in j["ventas"] if v["total"] == 300][0]

    assert pagada["cliente_nombre"] == tres_clientes[1]["nombre"]
    assert pagada["estado"] == "Pagada"
    assert pendiente["estado"] == "Pendiente"
    assert pendiente["pendiente"] == 200
    assert por_id


def test_las_ventas_de_un_gestor_no_se_mezclan(cliente_sesion, tres_clientes):
    """Un gestor no ve las ventas del otro, ni las que no tienen gestor."""
    _venta(cliente_sesion, tres_clientes[1]["id"], tres_clientes[0]["id"], 500, 500)
    _venta(cliente_sesion, tres_clientes[2]["id"], tres_clientes[1]["id"], 900, 900)
    _venta(cliente_sesion, tres_clientes[2]["id"], None, 111, 111)

    j = cliente_sesion.get("/api/gestores/%d/ventas" % tres_clientes[0]["id"]).get_json()
    assert [v["total"] for v in j["ventas"]] == [500]


def test_el_resumen_cuadra_con_las_ventas(cliente_sesion, tres_clientes):
    """La fila del panel muestra los totales, y tienen que ser los mismos."""
    from backend.clientes import set_gestor
    from backend.ventas import resumen_por_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 500, 500)
    _venta(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 300, 100)

    r = resumen_por_gestor()[gestor["id"]]
    assert r["ventas"] == 2
    assert r["total"] == 800
    assert r["pendiente"] == 200

    html = cliente_sesion.get("/gestores?vista=gestores").get_data(as_text=True)
    fila = re.search(r'<tr data-id="%d".*?</tr>' % gestor["id"], html, re.S).group(0)
    assert "800" in fila       # vendido
    assert "200" in fila       # pendiente


def test_gestor_sin_ventas_no_rompe_el_panel(cliente_sesion, tres_clientes):
    from backend.clientes import set_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")

    j = cliente_sesion.get("/api/gestores/%d/ventas" % gestor["id"]).get_json()
    assert j["ok"] is True and j["ventas"] == []

    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    assert 'id="ventasGestor%d"' % gestor["id"] in html


def test_el_endpoint_avisa_si_el_cliente_no_existe(cliente_sesion):
    r = cliente_sesion.get("/api/gestores/999999/ventas")
    assert r.status_code == 404
    assert r.get_json()["ok"] is False


def test_el_buscador_oculta_tambien_las_ventas(cliente_sesion):
    """La fila de ventas es otro <tr>: sin esto queda colgando de una fila oculta."""
    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    sel = re.search(r"function filtrarGestores.*?\n}\n", html, re.S).group(0)
    assert "ventasGestor' + fila.dataset.id" in sel
    assert "detalle.classList.add('d-none')" in sel


# ----------------------------
# Pendiente con gestor sigue siendo deuda
# ----------------------------
def test_pendiente_con_gestor_sigue_siendo_deuda(cliente_sesion, tres_clientes):
    """El gestor no cambia como se calcula la deuda: es el saldo del comprador."""
    from backend.deudas import list_debts
    from backend.ventas import get_sale

    gestor, comprador = tres_clientes[0], tres_clientes[1]
    venta = _venta(cliente_sesion, comprador["id"], gestor["id"], 500, 200)

    # la venta quedo pendiente y con su gestor
    assert get_sale(venta["id"])["gestor_id"] == gestor["id"]
    assert get_sale(venta["id"])["saldo"] == 300

    # y la deuda es del que compro, no del gestor
    deudas = [d for d in list_debts() if d["cliente_id"] == comprador["id"]]
    assert sum(d["monto_total"] for d in deudas) == 300
    assert not [d for d in list_debts() if d["cliente_id"] == gestor["id"]]


def test_ventas_sin_gestor_siguen_andando(cliente_sesion, tres_clientes):
    """Sin gestor no cambia nada: se guarda, descuenta stock y genera deuda."""
    from backend.deudas import list_debts
    from backend.productos import get_product
    from backend.ventas import get_sale

    venta = _venta(cliente_sesion, tres_clientes[1]["id"], None, 500, 200)

    assert get_sale(venta["id"])["gestor_id"] is None
    deudas = [d for d in list_debts() if d["cliente_id"] == tres_clientes[1]["id"]]
    assert sum(d["monto_total"] for d in deudas) == 300
    # el stock se descontó igual
    prod_id = get_sale(venta["id"])["productos_vendidos"][0]["id_producto"]
    assert float(get_product(prod_id)["cantidad"]) == 49


def test_el_pagada_y_la_pendiente_se_ven_distintas_en_el_panel(cliente_sesion, tres_clientes):
    """En el panel del gestor tiene que verse cuales están pendientes."""
    gestor = tres_clientes[0]
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 500, 500)
    _venta(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 300, 0)

    j = cliente_sesion.get("/api/gestores/%d/ventas" % gestor["id"]).get_json()
    estados = sorted(v["estado"] for v in j["ventas"])
    assert estados == ["Pagada", "Pendiente"]


# ----------------------------
# El filtro de fechas del panel
# ----------------------------
def _venta_con_fecha(cliente_sesion, comprador_id, gestor_id, total, pagado, dias_atras):
    """Venta con la fecha corrida, para probar el filtro."""
    from sqlalchemy import text
    from backend.db import engine
    from backend.ventas import ultima_venta

    _venta(cliente_sesion, comprador_id, gestor_id, total, pagado)
    venta = ultima_venta()
    with engine.begin() as c:
        c.execute(text("UPDATE ventas SET fecha = :f WHERE id = :i"),
                  {"f": datetime.now() - timedelta(days=dias_atras), "i": venta["id"]})
    return venta


def test_el_panel_muestra_el_dia_por_defecto(cliente_sesion, tres_clientes):
    """Sin tocar nada, el panel mira el dia de hoy, como /ventas."""
    gestor = tres_clientes[0]
    _venta_con_fecha(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 100, 100, 0)
    _venta_con_fecha(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 900, 900, 40)

    j = cliente_sesion.get("/api/gestores/%d/ventas" % gestor["id"]).get_json()
    assert [v["total"] for v in j["ventas"]] == [100]      # solo la de hoy
    assert j["ver_todo"] is False


def test_el_boton_ver_todo_muestra_el_historico(cliente_sesion, tres_clientes):
    gestor = tres_clientes[0]
    _venta_con_fecha(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 100, 100, 0)
    _venta_con_fecha(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 900, 900, 40)

    j = cliente_sesion.get("/api/gestores/%d/ventas?todo=1" % gestor["id"]).get_json()
    assert sorted(v["total"] for v in j["ventas"]) == [100, 900]
    assert j["ver_todo"] is True


def test_se_puede_pedir_otro_rango(cliente_sesion, tres_clientes):
    gestor = tres_clientes[0]
    _venta_con_fecha(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 100, 100, 0)
    _venta_con_fecha(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 900, 900, 5)

    hace10 = (date.today() - timedelta(days=10)).isoformat()
    r = cliente_sesion.get("/api/gestores/%d/ventas?fecha_inicio=%s&fecha_fin=%s"
                           % (gestor["id"], hace10, date.today().isoformat())).get_json()
    assert sorted(v["total"] for v in r["ventas"]) == [100, 900]


def test_el_resumen_respeta_el_rango(cliente_sesion, tres_clientes):
    """Si no, la fila mostra el total historico junto a una lista de un dia."""
    from backend.clientes import set_gestor

    gestor = tres_clientes[0]
    set_gestor(gestor["id"], True, "test")
    _venta_con_fecha(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 100, 100, 0)
    _venta_con_fecha(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 900, 900, 40)

    html = cliente_sesion.get("/gestores?vista=gestores").get_data(as_text=True)
    fila = re.search(r'<tr data-id="%d".*?</tr>' % gestor["id"], html, re.S).group(0)
    assert ">1<" in fila          # 1 venta del dia
    assert "900" not in fila      # la de hace 40 dias queda afuera

    html_todo = cliente_sesion.get("/gestores?vista=gestores&todo=1").get_data(as_text=True)
    fila_todo = re.search(r'<tr data-id="%d".*?</tr>' % gestor["id"], html_todo, re.S).group(0)
    assert ">2<" in fila_todo
    assert "1,000" in fila_todo   # 100 de hoy + 900 de hace 40 dias


def test_el_panel_trae_los_controles_de_fecha(cliente_sesion, tres_clientes):
    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    hoy = date.today().isoformat()
    assert 'id="fechaInicioGestor"' in html and 'name="fecha_inicio"' in html
    assert 'id="fechaFinGestor"' in html and 'name="fecha_fin"' in html
    # por defecto los dos extremos son hoy
    assert 'value="%s"' % hoy in html
    # y esta el boton para ver todo
    assert 'id="btnVerTodo"' in html
    assert "todo=1" in html


def test_el_rango_sobrevive_al_buscador(cliente_sesion, tres_clientes):
    """El filtro es del panel, no de la fila: cambiar de vista no lo borra."""
    gestor = tres_clientes[0]
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 100, 100)

    hace10 = (date.today() - timedelta(days=10)).isoformat()
    html = cliente_sesion.get("/gestores?fecha_inicio=%s&fecha_fin=%s&q=Gestor"
                             % (hace10, date.today().isoformat())).get_data(as_text=True)
    assert 'value="%s"' % hace10 in html
    assert "q=Gestor" in html


def test_la_cache_js_incluye_el_rango(cliente_sesion, tres_clientes):
    """Con la misma clave, cambiar de fecha seguia mostrando la lista vieja."""
    html = cliente_sesion.get("/gestores").get_data(as_text=True)
    assert "claveRangoGestor" in html
    sel = re.search(r"function claveRangoGestor.*?\n}\n", html, re.S).group(0)
    assert "FECHA_INICIO" in sel and "VER_TODO" in sel
    # y la peticion manda el rango
    assert "fecha_inicio=' + encodeURIComponent(FECHA_INICIO)" in html


def test_las_ventas_pagadas_y_pendientes_se_ven_juntas(cliente_sesion, tres_clientes):
    """Con gestor, pagada y pendiente conviven en la misma lista."""
    gestor = tres_clientes[0]
    _venta(cliente_sesion, tres_clientes[1]["id"], gestor["id"], 50000, 50000)
    _venta(cliente_sesion, tres_clientes[2]["id"], gestor["id"], 30000, 10000)

    j = cliente_sesion.get("/api/gestores/%d/ventas?todo=1" % gestor["id"]).get_json()
    por_total = {v["total"]: v for v in j["ventas"]}

    # la pagada queda como Pagada, asociada al gestor
    assert por_total[50000.0]["estado"] == "Pagada"
    assert por_total[50000.0]["pendiente"] == 0
    assert por_total[50000.0]["cliente_nombre"] == tres_clientes[1]["nombre"]
    # y la pendiente como Pendiente, con su saldo
    assert por_total[30000.0]["estado"] == "Pendiente"
    assert por_total[30000.0]["pendiente"] == 20000.0
    assert por_total[30000.0]["cliente_nombre"] == tres_clientes[2]["nombre"]


# ----------------------------
# La venta guarda el gestor
# ----------------------------
def test_la_venta_guarda_el_gestor(cliente_sesion, tres_clientes):
    """La venta puede venir con gestor o sin gestor."""
    from backend.clientes import set_gestor
    from backend.productos import guardar_producto
    from backend.categorias import agregar_categoria, list_categories
    from backend.ventas import get_sale, ultima_venta

    set_gestor(tres_clientes[0]["id"], True, "test")
    prod = guardar_producto("Producto Para Gestor", 10.0, 5, _cat(), "test")

    con_gestor = cliente_sesion.post("/ventas", data={
        "action": "registrar",
        "cliente_id": tres_clientes[1]["id"],
        "items": '[{"id_producto": %s, "nombre": "Producto Para Gestor",'
                 ' "cantidad": 1, "precio_unitario": 10}]' % prod["id"],
        "total": 10, "pagado": 10, "tipo_pago": "Efectivo",
        "gestor_id": tres_clientes[0]["id"],
    }, follow_redirects=True)
    assert con_gestor.status_code == 200

    # la venta existe y apunta al gestor
    venta = ultima_venta()
    assert venta["gestor_id"] == tres_clientes[0]["id"]
    assert get_sale(venta["id"])["gestor_id"] == tres_clientes[0]["id"]


def test_la_venta_puede_ir_sin_gestor(cliente_sesion, tres_clientes):
    from backend.productos import guardar_producto
    from backend.categorias import agregar_categoria, list_categories
    from backend.ventas import ultima_venta

    prod = guardar_producto("Producto Sin Gestor", 10.0, 5, _cat(), "test")
    r = cliente_sesion.post("/ventas", data={
        "action": "registrar",
        "cliente_id": tres_clientes[1]["id"],
        "items": '[{"id_producto": %s, "nombre": "Producto Sin Gestor",'
                 ' "cantidad": 1, "precio_unitario": 10}]' % prod["id"],
        "total": 10, "pagado": 10, "tipo_pago": "Efectivo",
    }, follow_redirects=True)
    assert r.status_code == 200
    assert ultima_venta()["gestor_id"] is None


def test_un_gestor_inexistente_no_rompe_la_venta(cliente_sesion, tres_clientes):
    """Un id de gestor que no existe avisa, no cuelga la app."""
    from backend.productos import guardar_producto
    from backend.categorias import agregar_categoria, list_categories
    from backend.ventas import ultima_venta

    prod = guardar_producto("Producto Gestor Malo", 10.0, 5, _cat(), "test")
    antes = ultima_venta()
    antes_id = antes["id"] if antes else None
    r = cliente_sesion.post("/ventas", data={
        "action": "registrar",
        "cliente_id": tres_clientes[1]["id"],
        "items": '[{"id_producto": %s, "nombre": "Producto Gestor Malo",'
                 ' "cantidad": 1, "precio_unitario": 10}]' % prod["id"],
        "total": 10, "pagado": 10, "tipo_pago": "Efectivo",
        "gestor_id": 999999,
    }, follow_redirects=True)
    assert r.status_code == 200
    assert b"no existe" in r.data
    # no se registro una venta nueva
    despues = ultima_venta()
    assert (despues["id"] if despues else None) == antes_id


def test_el_formulario_manda_el_gestor(cliente_sesion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    # el select vive fuera del form, asi que va en un campo oculto
    assert 'id="formGestorId"' in html and 'name="gestor_id"' in html
    js = re.search(r"function registrarVenta.*?\n}\n", html, re.S).group(0)
    assert "getElementById('gestorId')" in js
    assert "formGestorId" in js
