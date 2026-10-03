"""
Tests de la búsqueda de clientes del módulo /clientes.

El bug reportado: el selector de /ventas mostraba clientes que en /clientes
no aparecian al buscarlos. La causa era que filtrarClientes() solo miraba las
100 filas de la página actual del DOM, así que cualquier cliente fuera de esa
página era invisible para la búsqueda.

Ahora la búsqueda se hace en el servidor sobre TODOS los clientes, con el
mismo criterio (sin acentos, sin distinguir mayúsculas, por varias palabras)
que el filtro del selector de /ventas, para que no haya trigue entre módulos.
"""
import re

import pytest

from app import create_app
from backend.clientes import add_client, search_clients, count_clients, count_clients_search


@pytest.fixture
def cliente_sesion():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "test", "rol": "admin"}
        yield c


@pytest.fixture
def muchos_clientes():
    """150 clientes -> el que está al final queda fuera de la página 1 (100)."""
    for i in range(150):
        add_client(f"Cliente Numero {i:03d}", f"7000{i:04d}",
                   f"CI{i:05d}", None, f"CH{i:03d}")
    return 150


# ----------------------------
# search_clients()
# ----------------------------
def test_busca_por_id(muchos_clientes):
    """El ID es la clave fiable, tal como pidió el usuario."""
    objetivo = add_client("Buscado Por Id", "9990000", None, None, None)
    res = search_clients(str(objetivo["id"]))
    assert [c["id"] for c in res] == [objetivo["id"]]

    # también como prefijo, como hace el filtro del selector
    prefijo = str(objetivo["id"])[:3]
    assert objetivo["id"] in [c["id"] for c in search_clients(prefijo)]


def test_ignora_acentos_y_mayusculas():
    add_client("Rodrigáñez Pérez", "5550001", None, None, None)
    assert len(search_clients("rodriganez")) == 1
    assert len(search_clients("RODRIGANEZ")) == 1
    assert len(search_clients("RODRIGÁÑEZ")) == 1
    assert len(search_clients("perez")) == 1


def test_busca_por_telefono_ci_y_chapa():
    add_client("Datos Varios", "0987654321", "12345678", None, "ABC-999")
    assert len(search_clients("0987654321")) == 1
    assert len(search_clients("12345678")) == 1
    assert len(search_clients("abc-999")) == 1
    # los separadores no se ignoran: "ABC999" tampoco aparece en el selector
    assert search_clients("ABC999") == []


def test_varias_palabras_y_sin_resultados():
    add_client("Juan Perez Gomez", "5550002", None, None, None)
    add_client("Juan Ramirez", "5550003", None, None, None)
    assert len(search_clients("juan perez")) == 1
    assert len(search_clients("juan")) == 2
    assert search_clients("noexisteestecliente") == []
    assert search_clients("") == []
    assert search_clients("   ") == []


def test_ordenado_por_nombre(muchos_clientes):
    res = search_clients("Cliente Numero", limit=500)
    nombres = [c["nombre"] for c in res]
    assert nombres == sorted(nombres, key=lambda n: n.lower())


# ----------------------------
# Endpoint /clientes/buscar
# ----------------------------
def test_endpoint_buscar_ignora_la_paginacion(cliente_sesion, muchos_clientes):
    """Un cliente fuera de la página 1 tiene que aparecer en la búsqueda."""
    ultimo = add_client("Zzz Cliente Fuera De Pagina Uno", "7770000", None, None, None)
    total = count_clients()

    # el listado normal solo trae la página 1
    pagina1 = cliente_sesion.get("/clientes").get_data(as_text=True)
    assert "Zzz Cliente Fuera De Pagina Uno" not in pagina1
    assert total > 100

    # pero la búsqueda sí lo encuentra
    d = cliente_sesion.get("/clientes/buscar", query_string={"q": "Fuera De Pagina"}).get_json()
    assert [r["id"] for r in d["rows"]] == [ultimo["id"]]
    assert d["total"] == 1


def test_endpoint_buscar_por_id_devuelve_campos_completos(cliente_sesion, muchos_clientes):
    c = add_client("Cliente Con Datos", "0800123", "4455667", "Asuncion", "XYZ")
    d = cliente_sesion.get("/clientes/buscar", query_string={"q": str(c["id"])}).get_json()
    fila = d["rows"][0]
    assert fila["nombre"] == "Cliente Con Datos"
    assert fila["telefono"] == "0800123"
    assert fila["ci"] == "4455667"
    assert fila["direccion"] == "Asuncion"
    assert fila["chapa"] == "XYZ"
    assert fila["deuda"] == 0.0


def test_id_exacto_tiene_prioridad(cliente_sesion, muchos_clientes):
    """El ID completo debe salir primero, aunque el número coincida con muchos.

    Buscar un ID corto ("2") coincide con cientos de clientes y el que se busca
    puede quedar fuera de los primeros 100: ese era el "trigue" reportado. Con
    el ID completo, el exacto va siempre en primer lugar.
    """
    objetivo = add_client("Zurdo Final Del Alfabeto", "0991111", None, None, None)

    d = cliente_sesion.get("/clientes/buscar",
                           query_string={"q": str(objetivo["id"])}).get_json()
    assert d["rows"][0]["id"] == objetivo["id"]
    assert d["mostrando"] <= d["total"]

    # y también cuando el ID es un prefijo corto que matchea muchos campos
    prefijo = str(objetivo["id"])[:2]
    res = search_clients(prefijo, limit=100)
    assert objetivo["id"] in [c["id"] for c in res]

    # un ID completo tiene que ganarle a cualquier coincidencia parcial
    assert search_clients(str(objetivo["id"]), limit=1)[0]["id"] == objetivo["id"]


def test_id_exacto_gana_a_otro_que_empieza_igual(muchos_clientes):
    objetivo = add_client("Zzz Con Chapa 777", None, None, None, "777")
    add_client("Aaa Con Chapa 7778", None, None, None, "7778")
    res = search_clients("777", limit=5)
    assert res[0]["id"] == objetivo["id"]


def test_endpoint_informa_cuando_no_muestra_todo(cliente_sesion, muchos_clientes):
    d = cliente_sesion.get("/clientes/buscar", query_string={"q": "0"}).get_json()
    assert d["total"] > 100
    assert d["mostrando"] == 100


def test_endpoint_buscar_vacio(cliente_sesion, muchos_clientes):
    d = cliente_sesion.get("/clientes/buscar", query_string={"q": ""}).get_json()
    assert d == {"rows": [], "total": 0, "mostrando": 0}


def test_pagina_con_query_en_plantilla(cliente_sesion, muchos_clientes):
    """GET /clientes?q=... también filtra, por si se entra con la URL."""
    objetivo = add_client("Consulta Por Query", "1234321", None, None, None)
    html = cliente_sesion.get("/clientes", query_string={"q": "Query"}).get_data(as_text=True)
    assert "Consulta Por Query" in html
    assert "Cliente Numero 000" not in html
    assert objetivo["id"]


# ----------------------------
# Coherencia con el selector de /ventas
# ----------------------------
def test_mismo_criterio_que_el_selector_de_ventas(muchos_clientes):
    """Un cliente encontrado en un módulo debe estar en el otro.

    Reimplementa en Python el normalizar() del JS del selector para comparar.
    """
    import unicodedata

    def normaliza(t):
        s = "" if t is None else str(t).lower().strip()
        s = unicodedata.normalize("NFD", s)
        return "".join(c for c in s if unicodedata.category(c) != "Mn")

    objetivo = add_client("Coh.Paramós Ázur", "0999000", "7788990", None, "COH-1")

    # lo que ve el selector de /ventas (busca en nombre+tel+ci+chapa+id)
    heno = normaliza(" ".join(str(v) for v in [objetivo["id"], objetivo["nombre"],
                                              objetivo["telefono"], objetivo["ci"],
                                              objetivo["chapa"]]))
    assert "coh" in heno and "param" in heno and "azur" in heno

    # y /clientes lo encuentra con las mismas reglas
    for q in ("coh", "COH", "paramos", "azur", "0999000", str(objetivo["id"])):
        assert [c["id"] for c in search_clients(q)] == [objetivo["id"]], q


def test_el_html_ya_no_filtra_solo_el_dom(muchos_clientes):
    """Guarda: el filtro no puede volver a mirar solo las filas pintadas."""
    from app import create_app as _create
    app = _create()
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "t", "rol": "admin"}
        html = c.get("/clientes").get_data(as_text=True)

    assert "location.reload()" in html          # con búsqueda vacía se recarga
    assert "/clientes/buscar?q=" in html        # consulta al servidor
    assert "querySelectorAll('#tablaClientes tbody tr')" not in html
    # y el placeholder menciona los campos que ahora se searchean
    assert "telefono" in html and "CI" in html and "chapa" in html
