"""
Tests del filtro de fechas de la tabla "Ventas Registradas" (/ventas):
por defecto muestra el dia de hoy, y con fecha_inicio/fecha_fin muestra
solo las ventas del rango, respetando la paginacion.
"""
import re
from datetime import date, timedelta

import pytest
from sqlalchemy import text

from app import create_app
from backend import db
from backend.clientes import add_client
from backend.ventas import (
    normalizar_rango, rango_params, list_sales, count_sales, register_sale,
)


@pytest.fixture
def cliente_sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


@pytest.fixture
def producto(producto):
    """El producto del conftest viene con 10 de stock; estos tests necesitan más."""
    from backend.productos import update_product
    update_product(producto["id"], producto["nombre"], 1000, producto["precio"])
    return producto


def _venta(producto, dias_atras, nombre, hora="12:00:00", total=100, pagado=100):
    """Registra una venta de un cliente propio y la fecha a `dias_atras` dias atras."""
    cli = add_client(nombre=nombre, telefono="5550000", ci=None, direccion=None, chapa=None)
    venta = register_sale(
        cliente_id=cli["id"], total=total, pagado=pagado,
        usuario="test", tipo_pago="Efectivo",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 1, "precio_unitario": total}],
    )
    fecha = date.today() - timedelta(days=dias_atras)
    with db.engine.begin() as conn:
        conn.execute(
            text("UPDATE ventas SET fecha = :fecha WHERE id = :id"),
            {"fecha": f"{fecha.isoformat()} {hora}", "id": venta["id"]},
        )
    return venta


def _tabla(html):
    """Todo lo que sigue al <tbody id="bodyVentas">.

    No se corta en el primer </tbody> porque las filas de detalle llevan una
    tabla anidada de productos que cerraria ese tag antes de tiempo.
    """
    return html.split('id="bodyVentas"', 1)[1]


def _ids_de_la_tabla(html):
    """Ids de venta renderizados en la tabla de ventas registradas."""
    return set(re.findall(r'<td class="text-muted">#(\d+)</td>', _tabla(html)))


def _ids_en_orden(html):
    """Ids de venta en el orden en que aparecen en la tabla."""
    return re.findall(r'<td class="text-muted">#(\d+)</td>', _tabla(html))


# ----------------------------
# normalizar_rango
# ----------------------------
def test_normalizar_rango_por_defecto_hoy():
    fi, ff, invertido = normalizar_rango(None, None)
    hoy = date.today()
    assert (fi, ff, invertido) == (hoy, hoy, False)


def test_normalizar_rango_invertido_se_recorta():
    hoy = date.today()
    fi, ff, invertido = normalizar_rango((hoy + timedelta(days=5)).isoformat(), hoy.isoformat())
    assert invertido is True
    assert fi == ff == hoy


def test_normalizar_rango_ignora_basura():
    fi, ff, invertido = normalizar_rango("ayer", "2026-13-45")
    assert (fi, ff, invertido) == (date.today(), date.today(), False)


def test_rango_params_es_intervalo_semabierto():
    fi, ff, _ = normalizar_rango("2026-09-24", "2026-09-24")
    p = rango_params(fi, ff)
    assert p["rango_desde"] == "2026-09-24 00:00:00"
    assert p["rango_hasta"] == "2026-09-25 00:00:00"


# ----------------------------
# list_sales / count_sales
# ----------------------------
def test_list_sales_filtra_por_rango(producto):
    dentro = _venta(producto, 2, "Cliente Dentro")
    fuera = _venta(producto, 8, "Cliente Fuera")

    hoy = date.today()
    r = list_sales(fecha_inicio=hoy - timedelta(days=3), fecha_fin=hoy)
    assert [v["id"] for v in r] == [dentro["id"]]
    assert count_sales(fecha_inicio=hoy - timedelta(days=3), fecha_fin=hoy) == 1
    assert count_sales() == 2
    assert fuera["id"] not in [v["id"] for v in r]


def test_list_sales_rango_incluye_todo_el_ultimo_dia(producto):
    """Las ventas a las 23:59 del último día deben entrar en el rango."""
    tarde = _venta(producto, 0, "Cliente Tarde", hora="23:59:59")
    manana = _venta(producto, -1, "Cliente Manana", hora="00:00:01")

    hoy = date.today()
    r = list_sales(fecha_inicio=hoy, fecha_fin=hoy)
    assert [v["id"] for v in r] == [tarde["id"]]
    assert manana["id"] not in [v["id"] for v in r]


def test_list_sales_orden_por_defecto_por_fecha(producto):
    """El orden por defecto sigue siendo ascendente; 'desc' invierte."""
    vieja = _venta(producto, 5, "Cliente Vieja")
    nueva = _venta(producto, 0, "Cliente Nueva")

    asc = [v["id"] for v in list_sales()]
    desc = [v["id"] for v in list_sales(orden="desc")]
    assert asc.index(vieja["id"]) < asc.index(nueva["id"])
    assert desc.index(nueva["id"]) < desc.index(vieja["id"])


def test_list_sales_puede_ordenar_por_id(producto):
    """ordenar_por='id' ordena por id, no por fecha."""
    a = _venta(producto, 5, "Cliente A")   # id menor, fecha mas vieja
    b = _venta(producto, 0, "Cliente B")   # id mayor, fecha mas reciente
    c = _venta(producto, 9, "Cliente C")   # id mayor que B, fecha mas vieja

    por_id_desc = [v["id"] for v in list_sales(ordenar_por="id", orden="desc")]
    por_id_asc = [v["id"] for v in list_sales(ordenar_por="id", orden="asc")]
    assert por_id_desc == [c["id"], b["id"], a["id"]]
    assert por_id_asc == [a["id"], b["id"], c["id"]]

    # 'fecha' ignora el id: C es la mas vieja, asi que va primera en asc
    por_fecha_asc = [v["id"] for v in list_sales(ordenar_por="fecha", orden="asc")]
    assert por_fecha_asc[0] == c["id"]


def test_list_sales_orden_por_id_dentro_del_rango(producto):
    _venta(producto, 0, "Cliente Hoy")
    _venta(producto, 2, "Cliente Antes")
    _venta(producto, 9, "Cliente Fuera")

    hoy = date.today()
    r = list_sales(fecha_inicio=hoy - timedelta(days=3), fecha_fin=hoy,
                   orden="desc", ordenar_por="id")
    assert len(r) == 2
    assert r[0]["id"] > r[1]["id"]


def test_list_sales_pagina_dentro_del_rango(producto):
    for i in range(3):
        _venta(producto, 1, f"Cliente P{i}")
    _venta(producto, 9, "Cliente Fuera")

    hoy = date.today()
    r = list_sales(limit=2, offset=0, fecha_inicio=hoy - timedelta(days=2),
                   fecha_fin=hoy, orden="desc")
    assert len(r) == 2
    assert count_sales(fecha_inicio=hoy - timedelta(days=2), fecha_fin=hoy) == 3


# ----------------------------
# Ruta /ventas
# ----------------------------
def test_ventas_por_defecto_muestra_hoy(cliente_sesion, producto):
    _venta(producto, 0, "Cliente Hoy")
    _venta(producto, 3, "Cliente Antes")

    r = cliente_sesion.get("/ventas")
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    tabla = _tabla(cuerpo)
    assert f'value="{date.today().isoformat()}"' in cuerpo
    assert len(_ids_de_la_tabla(cuerpo)) == 1
    assert "Cliente Hoy" in tabla
    assert "Cliente Antes" not in tabla


def test_ventas_ordena_por_id_descendente(cliente_sesion, producto):
    """La tabla de ventas registradas va de mayor a menor ID, no por fecha."""
    # Ids 1..4 con fechas en desorden, para que ID y fecha den ordenes distintos.
    _venta(producto, 9, "Cliente Dia9")   # id 1, el mas viejo
    _venta(producto, 0, "Cliente Hoy")    # id 2, el mas reciente
    _venta(producto, 5, "Cliente Dia5")   # id 3
    _venta(producto, 2, "Cliente Dia2")   # id 4, el mayor id

    hoy = date.today()
    r = cliente_sesion.get("/ventas", query_string={
        "fecha_inicio": (hoy - timedelta(days=15)).isoformat(),
        "fecha_fin": hoy.isoformat(),
    })
    assert r.status_code == 200
    ids = [int(i) for i in _ids_en_orden(r.get_data(as_text=True))]
    assert len(ids) == 4
    assert ids == sorted(ids, reverse=True)
    # Si ordenara por fecha, el id 2 (hoy) seria el primero: no debe pasar.
    assert ids[0] == 4


def test_ventas_filtra_por_rango(cliente_sesion, producto):
    ayer = _venta(producto, 1, "Cliente Ayer")
    hace4 = _venta(producto, 4, "Cliente Hace4")
    hace9 = _venta(producto, 9, "Cliente Hace9")

    hoy = date.today()
    r = cliente_sesion.get("/ventas", query_string={
        "fecha_inicio": (hoy - timedelta(days=5)).isoformat(),
        "fecha_fin": hoy.isoformat(),
    })
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    tabla = _tabla(cuerpo)
    ids = _ids_de_la_tabla(cuerpo)
    assert ids == {str(ayer["id"]), str(hace4["id"])}
    assert str(hace9["id"]) not in ids
    assert "Cliente Ayer" in tabla and "Cliente Hace4" in tabla
    assert "Cliente Hace9" not in tabla


def test_ventas_rango_invertido_avisa(cliente_sesion, producto):
    _venta(producto, 0, "Cliente Hoy")
    hoy = date.today()

    r = cliente_sesion.get("/ventas", query_string={
        "fecha_inicio": (hoy + timedelta(days=1)).isoformat(),
        "fecha_fin": hoy.isoformat(),
    })
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    assert "no puede ser mayor" in cuerpo
    assert f'value="{hoy.isoformat()}"' in cuerpo
    assert "Cliente Hoy" in cuerpo


def test_ventas_contador_refleja_el_rango(cliente_sesion, producto):
    _venta(producto, 0, "Cliente Hoy")
    _venta(producto, 0, "Cliente Hoy2")
    _venta(producto, 7, "Cliente Viejo")

    r = cliente_sesion.get("/ventas")
    cuerpo = r.get_data(as_text=True)
    assert "2 ventas en el periodo" in cuerpo


def test_ventas_paginacion_conserva_el_rango(cliente_sesion, producto):
    for i in range(60):
        _venta(producto, 1, f"Cliente {i}")
    _venta(producto, 0, "Cliente Hoy")

    hoy = date.today()
    inicio = (hoy - timedelta(days=2)).isoformat()
    r = cliente_sesion.get("/ventas", query_string={
        "fecha_inicio": inicio, "fecha_fin": hoy.isoformat(), "page": 2,
    })
    assert r.status_code == 200
    cuerpo = r.get_data(as_text=True)
    tabla = _tabla(cuerpo)
    # 60 ventas ayer + 1 hoy = 61 -> 2 paginas de 50
    assert "Cliente Hoy" not in tabla
    assert len(_ids_de_la_tabla(cuerpo)) == 11
    assert "page=2" in cuerpo and f"fecha_inicio={inicio}" in cuerpo


def test_busqueda_respeta_el_rango(cliente_sesion, producto):
    _venta(producto, 0, "Buscable Hoy")
    _venta(producto, 5, "Buscable Viejo")

    hoy = date.today()
    r = cliente_sesion.get("/ventas/buscar", query_string={
        "q": "Buscable",
        "fecha_inicio": (hoy - timedelta(days=1)).isoformat(),
        "fecha_fin": hoy.isoformat(),
    })
    assert r.status_code == 200
    clientes = [row["cliente"] for row in r.get_json()["rows"]]
    assert clientes == ["Buscable Hoy"]


def test_eliminar_conserva_el_rango(cliente_sesion, producto):
    """Tras eliminar, el usuario vuelve al mismo rango que estaba viendo."""
    v = _venta(producto, 4, "Cliente Objetivo")

    hoy = date.today()
    inicio = (hoy - timedelta(days=5)).isoformat()
    fin = (hoy - timedelta(days=3)).isoformat()
    r = cliente_sesion.post("/ventas", data={
        "action": "eliminar", "venta_id": v["id"],
        "fecha_inicio": inicio, "fecha_fin": fin,
    })
    assert r.status_code == 302
    assert f"fecha_inicio={inicio}" in r.headers["Location"]
    assert f"fecha_fin={fin}" in r.headers["Location"]


# ----------------------------
# Factura de la ultima venta
# ----------------------------
def test_ultima_venta_devuelve_la_mas_reciente(cliente_sesion, producto):
    from backend.ventas import ultima_venta

    assert ultima_venta() is None

    _venta(producto, 9, "Cliente Viejo")
    nueva = _venta(producto, 0, "Cliente Reciente")

    u = ultima_venta()
    assert u["id"] == nueva["id"]
    assert u["cliente_nombre"] == "Cliente Reciente"
    # Aunque sea la mas reciente por id, no por fecha
    _venta(producto, 5, "Cliente Intermedio")
    assert ultima_venta()["id"] > nueva["id"]


def test_boton_factura_ultima_venta(cliente_sesion, producto):
    """El boton de abajo de Registrar Venta apunta a la ultima venta."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)

    # Sin ventas: boton deshabilitado
    assert 'id="btnUltimaFactura"' in html
    assert re.search(r'id="btnUltimaFactura".*?disabled', html, re.S)

    v = _venta(producto, 0, "Cliente Facturable")
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    btn = re.search(r'<button[^>]*id="btnUltimaFactura".*?</button>', html, re.S).group(0)
    assert "disabled" not in btn
    assert f"#{v['id']}" in btn
    assert f'data-id="{v["id"]}"' in btn
    assert "abrirModalFactura(this.dataset.id)" in btn
    assert "Cliente Facturable" in html


def test_boton_abrir_ultima_factura_directo(cliente_sesion, producto):
    """El boton 'Abrir PDF ultima factura' abre el PDF sin pasar por el modal."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)

    # Sin ventas: boton deshabilitado
    assert 'id="btnAbrirUltimaFactura"' in html
    assert re.search(r'id="btnAbrirUltimaFactura".*?disabled', html, re.S)

    v = _venta(producto, 0, "Cliente Directo")
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    btn = re.search(r'<button[^>]*id="btnAbrirUltimaFactura".*?</button>', html, re.S).group(0)
    assert "disabled" not in btn
    assert f'data-id="{v["id"]}"' in btn
    assert "abrirFacturaDirecta(this.dataset.id)" in btn


def test_factura_se_abre_en_navegador_con_token(cliente_sesion, producto, monkeypatch, tmp_path):
    """/descargar devuelve URLs firmadas: visor HTML (imprimible) y PDF."""
    from urllib.parse import urlparse

    # No ensuciar la carpeta real de Documentos del usuario.
    monkeypatch.setenv("HOME", str(tmp_path))

    v = _venta(producto, 0, "Cliente Navegador")
    r = cliente_sesion.get(f"/factura/{v['id']}/descargar")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert data["archivo"].endswith(".pdf")
    assert f"/factura/{v['id']}/visor" in data["url"]
    assert f"/factura/{v['id']}/ver" in data["pdf_url"]

    # Ambas URLs sirven sin sesion de login (app / navegador externo)
    with cliente_sesion.session_transaction() as s:
        s.clear()

    pv = urlparse(data["url"])
    rvisor = cliente_sesion.get(f"{pv.path}?{pv.query}")
    assert rvisor.status_code == 200
    assert rvisor.mimetype == "text/html"
    html_visor = rvisor.get_data(as_text=True)
    assert "window.print()" in html_visor
    assert "Cliente Navegador" in html_visor

    pp = urlparse(data["pdf_url"])
    rpdf = cliente_sesion.get(f"{pp.path}?{pp.query}")
    assert rpdf.status_code == 200
    assert rpdf.mimetype == "application/pdf"

    # Token invalido: no se filtra la factura
    assert cliente_sesion.get(f"/factura/{v['id']}/ver?t=token-falso").status_code == 403
    assert cliente_sesion.get(f"/factura/{v['id']}/visor?t=token-falso").status_code == 403


def test_registrar_venta_va_directo_a_la_factura(cliente_sesion, producto):
    """Tras registrar, el redirect lleva ?factura=<id> para abrir el modal."""
    cli = add_client(nombre="Cliente Nuevo", telefono="5550000",
                     ci=None, direccion=None, chapa=None)
    r = cliente_sesion.post("/ventas", data={
        "action": "registrar",
        "cliente_id": cli["id"],
        "items": '[{"id_producto": %d, "nombre": "P", "cantidad": 1, "precio_unitario": 100}]' % producto["id"],
        "total": 100, "pagado": 100, "tipo_pago": "Efectivo",
    })
    assert r.status_code == 302
    venta_id = int(re.search(r"factura=(\d+)", r.headers["Location"]).group(1))

    # La pagina de destino auto-abre la factura de esa venta
    html = cliente_sesion.get(f"/ventas?factura={venta_id}").get_data(as_text=True)
    assert f"abrirModalFactura({venta_id})" in html
    assert "searchParams.delete('factura')" in html

    # Sin el parametro no hay auto-apertura
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "searchParams.delete('factura')" not in html


def test_venta_con_error_no_pide_factura(cliente_sesion):
    """Si el registro falla, no se redirige a abrir una factura."""
    r = cliente_sesion.post("/ventas", data={
        "action": "registrar", "cliente_id": "no-existe",
        "items": "[]", "total": 0, "pagado": 0, "tipo_pago": "Efectivo",
    })
    assert r.status_code == 302
    assert "factura=" not in r.headers["Location"]


def test_eliminar_venta_no_pide_factura(cliente_sesion, producto):
    v = _venta(producto, 0, "Cliente A Eliminar")
    r = cliente_sesion.post("/ventas", data={"action": "eliminar", "venta_id": v["id"]})
    assert r.status_code == 302
    assert "factura=" not in r.headers["Location"]


# ----------------------------
# Detalle de productos por venta
# ----------------------------
def test_detalle_productos_muestra_cantidades(cliente_sesion, producto):
    """Cada venta trae su fila de detalle con producto y cantidad."""
    from backend.ventas import register_sale
    cli = add_client(nombre="Cliente Detalle", telefono="5550000",
                     ci=None, direccion=None, chapa=None)
    v = register_sale(
        cliente_id=cli["id"], total=1800, pagado=1800, usuario="test",
        tipo_pago="Efectivo",
        productos=[
            {"id_producto": producto["id"], "nombre": "Split EKO 2T",
             "cantidad": 2, "precio_unitario": 530},
            {"id_producto": producto["id"], "nombre": "Ventilador JMD 12p",
             "cantidad": 3, "precio_unitario": 247},
        ],
    )
    html = cliente_sesion.get("/ventas").get_data(as_text=True)

    # Boton resumen: 2 productos, 5 unidades
    assert f'data-detalle="detalleVenta{v["id"]}"' in html
    assert "2 prod." in html
    assert "5 u." in html

    # Fila de detalle oculta por defecto
    assert f'<tr id="detalleVenta{v["id"]}" class="detalle-venta d-none">' in html
    # El colspan se calcula desde el encabezado en vez de ir fijo: si se agrega
    # una columna (el gestor) y no se ajusta, el detalle queda desalineado.
    encabezado = re.search(
        r'<table class="table table-hover mb-0">.*?</thead>', html, re.S).group(0)
    columnas = len(re.findall(r"<th[ >]", encabezado))
    assert columnas == 9
    assert f'colspan="{columnas}"' in html

    # Productos y cantidades
    assert "Split EKO 2T" in html and "Ventilador JMD 12p" in html
    assert "<td class=\"text-end\">2</td>" in html
    assert "<td class=\"text-end\">3</td>" in html
    assert "$530.00" in html and "$1,060.00" in html


def test_venta_sin_productos_muestra_guion(cliente_sesion):
    """Una venta sin lineas de producto no ofrece boton de detalle."""
    from backend.ventas import register_sale
    cli = add_client(nombre="Cliente Sin Nada", telefono="5550000",
                     ci=None, direccion=None, chapa=None)
    v = register_sale(cliente_id=cli["id"], total=0, pagado=0,
                      usuario="test", tipo_pago="Efectivo", productos=[])
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    fila = re.search(
        r'<tr id="detalleVenta%s".*?</td>\s*</tr>' % v["id"], html, re.S
    ).group(0)
    assert "Esta venta no tiene productos registrados." in fila
    assert "data-detalle" not in fila
    assert "prod." not in fila


def test_busqueda_incluye_productos(cliente_sesion, producto):
    """/ventas/buscar devuelve los productos para pintar el detalle."""
    from datetime import date as _d
    v = _venta(producto, 0, "Cliente Buscable", total=530, pagado=530)
    hoy = _d.today().isoformat()
    d = cliente_sesion.get("/ventas/buscar", query_string={
        "q": "Cliente Buscable", "fecha_inicio": hoy, "fecha_fin": hoy,
    }).get_json()

    filas = [r for r in d["rows"] if r["id"] == v["id"]]
    assert filas, "la venta deberia aparecer en la busqueda"
    prods = filas[0]["productos"]
    assert len(prods) == 1
    assert prods[0]["nombre"] == producto["nombre"]
    assert prods[0]["cantidad"] == 1.0
    assert prods[0]["subtotal"] == 530.0


def test_productos_venta_normaliza_entradasraras():
    """_productos_venta tolera texto JSON, campos faltantes y basura."""
    from app import _productos_venta

    # texto JSON crudo, como lo devuelve el driver
    assert _productos_venta('[{"nombre":"A","cantidad":2,"precio_unitario":10}]') == [
        {"nombre": "A", "cantidad": 2.0, "precio": 10.0, "subtotal": 20.0}
    ]
    # sin subtotal -> se calcula
    assert _productos_venta([{"nombre": "B", "cantidad": 3, "precio_unitario": 5}])[0]["subtotal"] == 15.0
    # basura
    assert _productos_venta(None) == []
    assert _productos_venta("[]") == []
    assert _productos_venta("no soy json") == []
    assert _productos_venta({"a": 1}) == []
    assert _productos_venta([1, "x", None]) == []
    # cantidad no numerica se descarta esa linea
    assert _productos_venta([{"nombre": "C", "cantidad": "abc"}]) == []


# ----------------------------
# Factura: no debe cruzarse de venta
# ----------------------------
def test_factura_no_se_cruza_entre_ventas(cliente_sesion, producto):
    """Cada endpoint de factura opera sobre la venta que se le pide.

    Es el bug reportado: al facturar una venta PENDIENTE aparecian los datos
    de otra venta PAGADA.
    """
    from backend.ventas import register_sale
    cli = add_client(nombre="Cliente Facturado", telefono="5550000",
                     ci=None, direccion=None, chapa=None)
    # una pagada y una pendiente, del mismo cliente y monto, como en el reporte
    pagada = register_sale(cliente_id=cli["id"], total=100, pagado=100,
                           usuario="test", tipo_pago="Efectivo",
                           productos=[{"id_producto": producto["id"], "nombre": "Pagada",
                                       "cantidad": 1, "precio_unitario": 100}])
    pendiente = register_sale(cliente_id=cli["id"], total=100, pagado=0,
                              usuario="test", tipo_pago="Efectivo",
                              productos=[{"id_producto": producto["id"], "nombre": "Pendiente",
                                          "cantidad": 1, "precio_unitario": 100}])
    assert pagada["id"] != pendiente["id"]

    for vid, esperado in ((pendiente["id"], "Pendiente"), (pagada["id"], "Pagada")):
        d = cliente_sesion.get(f"/api/ventas/{vid}/datos_factura").get_json()
        assert d["venta"]["id"] == vid
        # el saldo distingue una de otra
        assert float(d["venta"]["saldo"]) == (0.0 if esperado == "Pagada" else 100.0)

    # guardar datos de factura solo toca esa venta
    r = cliente_sesion.post(f"/api/ventas/{pendiente['id']}/factura_datos",
                            json={"observaciones": "solo la pendiente", "vendedor": "V"})
    assert r.get_json()["ok"] is True

    a = cliente_sesion.get(f"/api/ventas/{pendiente['id']}/datos_factura").get_json()
    b = cliente_sesion.get(f"/api/ventas/{pagada['id']}/datos_factura").get_json()
    assert a["venta"]["observaciones"] == "solo la pendiente"
    assert b["venta"]["observaciones"] in (None, ""), "no debe filtrarse a la otra venta"
    assert a["venta"]["vendedor"] == "V"
    assert b["venta"]["vendedor"] in (None, "")


def test_js_no_colisiona_con_el_id_del_modal():
    """La variable JS no puede llamarse igual al id del elemento.

    'facturaVentaId' es el id del <span> del titulo; un global con ese
    nombre choca con la propiedad que el navegador deriva del id.
    """
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "t", "rol": "admin"}
        html = c.get("/ventas").get_data(as_text=True)

    assert 'id="facturaVentaId"' in html
    assert "let facturaVentaIdActual = null;" in html
    # ningun uso de la variable vieja
    assert "= facturaVentaId;" not in html
    assert "+ facturaVentaId +" not in html


def test_modulo_tiene_guard_de_race():
    """Abrir dos facturas rápido no debe dejar datos de la venta anterior."""
    from app import create_app
    app = create_app()
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "t", "rol": "admin"}
        html = c.get("/ventas").get_data(as_text=True)

    assert "let facturaSeq = 0;" in html
    assert "miSeq !== facturaSeq" in html
    assert "if (!ok || miSeq !== facturaSeq) return;" in html
    # y el modal dice si la venta esta pagada o pendiente
    assert 'id="facturaVentaEstado"' in html
    assert "PENDIENTE" in html and "PAGADA" in html
