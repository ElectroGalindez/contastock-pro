"""
Tests del selector de clientes del registro de ventas (/ventas):
la lista completa viaja embebida en el HTML y se filtra en el navegador.
"""
import json
import re

import pytest

from app import create_app
from backend.clientes import add_client, clientes_para_selector, count_clients


@pytest.fixture
def cliente_sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


@pytest.fixture
def poblacion():
    add_client("Abel Fernandez Diaz", "5551111", "12345678", None, None)
    add_client("Abel Antonio Reina", "5552222", None, None, None)
    add_client("ABELINA Zambrano", "5553333", None, None, "ABC-123")
    add_client("Zoraida Pettit", "5554444", None, None, None)


def _datos(html):
    m = re.search(r'<script type="application/json" id="datosClientes">(.*?)</script>', html, re.S)
    assert m, "no se encontro el bloque de datos de clientes"
    return json.loads(m.group(1))


# ----------------------------
# clientes_para_selector()
# ----------------------------
def test_trae_todos_los_clientes(poblacion):
    datos = clientes_para_selector()
    assert len(datos) == count_clients()
    assert [c["n"] for c in datos] == sorted(c["n"] for c in datos)


def test_usa_claves_cortas_y_trae_lo_necesario(poblacion):
    c = next(c for c in clientes_para_selector() if c["n"] == "ABELINA Zambrano")
    assert set(c) == {"i", "n", "t", "c", "h", "d", "deb", "g"}
    assert c["t"] == "5553333"
    assert c["h"] == "ABC-123"
    assert c["d"] == 0.0
    assert c["deb"] is False
    assert c["g"] is False


def test_la_deuda_sale_de_la_tabla_deudas_no_de_la_columna(poblacion):
    """'d' y 'deb' se calculan con las deudas pendientes reales.

    clientes.deuda_total quedo desactualizada: marcaba como deudores a clientes
    que ya no deben nada, y por eso la alerta de /ventas salia de mas.
    """
    from sqlalchemy import text
    from backend import db
    from backend.deudas import add_debt

    objetivo = clientes_para_selector()[0]
    # columna desactualizada: positiva, pero sin ninguna deuda pendiente
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE clientes SET deuda_total = 9999"))
    datos = {c["i"]: c for c in clientes_para_selector()}
    assert datos[objetivo["i"]]["d"] == 0.0
    assert datos[objetivo["i"]]["deb"] is False

    # con una deuda pendiente real, ahora si aparece
    add_debt(objetivo["i"], monto_total=250.5, usuario="test")
    datos = {c["i"]: c for c in clientes_para_selector()}
    assert datos[objetivo["i"]]["d"] == pytest.approx(250.5)
    assert datos[objetivo["i"]]["deb"] is True

    # las pagadas no cuentan
    add_debt(objetivo["i"], monto_total=100.0, estado="pagada", usuario="test")
    datos = {c["i"]: c for c in clientes_para_selector()}
    assert datos[objetivo["i"]]["d"] == pytest.approx(250.5)


def test_deuda_se_normaliza_a_float(poblacion):
    from backend import db
    from backend.deudas import add_debt

    for objetivo in clientes_para_selector()[:3]:
        add_debt(objetivo["i"], monto_total=150.5, usuario="test")

    datos = clientes_para_selector()
    assert all(isinstance(c["d"], float) for c in datos)
    assert all(isinstance(c["deb"], bool) for c in datos)
    assert sum(c["d"] for c in datos) == pytest.approx(150.5 * 3)


def test_no_depende_de_datos_previos_al_estado(poblacion):
    assert clientes_para_selector() == clientes_para_selector()


# ----------------------------
# /ventas
# ----------------------------
def test_ventas_embebe_todos_los_clientes(cliente_sesion, poblacion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    datos = _datos(html)
    assert len(datos) == count_clients()
    assert {c["n"] for c in datos} >= {
        "Abel Fernandez Diaz", "Abel Antonio Reina", "ABELINA Zambrano", "Zoraida Pettit"
    }


def test_ventas_no_usa_un_select_de_clientes(cliente_sesion, poblacion):
    """Con miles de clientes el HTML ya no lleva un <option> por cliente."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert 'id="selectCliente"' not in html
    assert 'id="buscarCliente"' in html
    assert 'id="listaClientes"' in html
    total = count_clients()
    assert str(total) + " clientes registrados" in html


def test_ventas_tiene_el_input_oculto(cliente_sesion, poblacion):
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert re.search(r'<input type="hidden" id="clienteId"', html)


def test_ventas_cablea_el_evento_input(cliente_sesion, poblacion):
    """Regresion: el filtro debe engancharse al evento 'input', no a un
    atributo inline, o el buscador no responde a nada."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert re.search(
        r"getElementById\('buscarCliente'\)\.addEventListener\('input'", html
    ), "no hay listener del evento input sobre #buscarCliente"


def test_ventas_filtra_solo_en_el_navegador(cliente_sesion, poblacion):
    """Ya no debe haber fetch al filtrar: la lista completa va embebida."""
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    assert "/api/clientes/buscar" not in html


def test_ventas_preserva_caracteres_especiales_del_json(cliente_sesion):
    add_client('Cliente "Comilla" & <script>', None, None, None, None)
    html = cliente_sesion.get("/ventas").get_data(as_text=True)
    datos = _datos(html)
    assert any(c["n"] == 'Cliente "Comilla" & <script>' for c in datos)


def test_ventas_no_puede_romperse_con_un_nombre_malicioso(cliente_sesion):
    """Un nombre con '</script>' no debe poder cerrar el bloque de datos.

    El JSON va dentro de un <script type="application/json">, asi que un
    '</script>' sin escapararia del tag y ejecutaria JS. Jinja |tojson lo
    neutraliza con \\u003c.
    """
    payloads = [
        '</script><script>alert("XSS")</script>',
        '</SCRIPT ><img src=x onerror=alert(1)>',
        '</script\n><svg/onload=alert(1)>',
    ]
    for p in payloads:
        add_client(p, None, None, None, None)

    html = cliente_sesion.get("/ventas").get_data(as_text=True)

    # 1) el bloque de datos no contiene un cierre de script real
    crudo = re.search(
        r'<script type="application/json" id="datosClientes">(.*?)</script>', html, re.S
    ).group(1)
    assert "</script" not in crudo.lower()
    assert "<script" not in crudo.lower()

    # 2) ademas el nombre sobrevive intacto al round-trip
    nombres = {c["n"] for c in json.loads(crudo)}
    for p in payloads:
        assert p in nombres

    # 3) y el payload queda escapado en el HTML servido
    assert "alert(\"XSS\")" not in html.split('id="datosClientes"')[1][:2000] or "\\u003c" in crudo
