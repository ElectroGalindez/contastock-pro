"""Conversión de tipos de SQLite a PostgreSQL del migrador a Neon.

El migrador copia la base local a Neon insertando los ids a mano. Postgres es
más estricto que SQLite, así que si se pasa un valor sin convertir falla la
carga: un BOOLEAN guardado como 0, un NUMERIC float, una fecha en texto o el
JSON como cadena. Estos tests fijan la conversión de cada caso.
"""
import json
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Boolean, DateTime, Integer, JSON, Numeric, String, Text

from scripts.migrar_local_a_neon import ProblemaConversion, convertir, convertir_fila


# ----------------------------
# Boolean
# ----------------------------
@pytest.mark.parametrize("valor,esperado", [
    (1, True), (0, False), (True, True), (False, False),
    ("1", True), ("0", False), ("true", True), ("false", False),
    ("t", True), ("f", False), ("SI", True), ("no", False),
])
def test_boolean(valor, esperado):
    assert convertir(valor, Boolean()) is esperado


def test_boolean_nulo_queda_nulo():
    assert convertir(None, Boolean()) is None


def test_boolean_ilexible_avisa():
    with pytest.raises(ProblemaConversion):
        convertir("quiza", Boolean())


# ----------------------------
# Numeric
# ----------------------------
def test_numeric_de_float():
    # float directo puede traer error binario; el Decimal sale del texto
    assert convertir(0.1, Numeric()) == Decimal("0.1")


def test_numeric_de_entero_y_texto():
    assert convertir(5, Numeric()) == Decimal("5")
    assert convertir("123.45", Numeric()) == Decimal("123.45")


def test_numeric_nulo_y_ilexible():
    assert convertir(None, Numeric()) is None
    with pytest.raises(ProblemaConversion):
        convertir("mucho", Numeric())


# ----------------------------
# Fechas
# ----------------------------
def test_fecha_desde_texto_de_sqlite():
    valor = convertir("2026-09-28 12:12:36.160762", DateTime())
    assert valor == datetime(2026, 9, 28, 12, 12, 36, 160762)


@pytest.mark.parametrize("texto,esperado", [
    ("2025-10-08 15:55:21", datetime(2025, 10, 8, 15, 55, 21)),
    ("2025-10-08T15:55:21", datetime(2025, 10, 8, 15, 55, 21)),
    ("2025-10-08", datetime(2025, 10, 8)),
    ("2025-10-08 15:55", datetime(2025, 10, 8, 15, 55)),
])
def test_fecha_en_varios_formatos(texto, esperado):
    assert convertir(texto, DateTime()) == esperado


def test_fecha_vacia_es_nula():
    assert convertir("", DateTime()) is None


def test_fecha_ilexible_avisa():
    with pytest.raises(ProblemaConversion):
        convertir("el jueves", DateTime())


# ----------------------------
# JSON
# ----------------------------
def test_json_desde_texto():
    original = [{"nombre": "Cafe", "cantidad": 2.0, "precio_unitario": 250.0}]
    assert convertir(json.dumps(original), JSON()) == original


def test_json_vacio_es_nulo():
    assert convertir("", JSON()) is None
    assert convertir("   ", JSON()) is None


def test_json_roto_avisa():
    """Un JSON ilegible es un dato corrupto: avisar es mejor que mandarlo roto."""
    with pytest.raises(ProblemaConversion):
        convertir("{no es json", JSON())


def test_json_ya_parsed_no_se_toca():
    valor = {"a": 1}
    assert convertir(valor, JSON()) is valor


# ----------------------------
# Enteros y texto
# ----------------------------
def test_entero_desde_texto():
    assert convertir("42", Integer()) == 42


def test_texto_pasa_tal_cual():
    assert convertir("Cafe 2x", String()) == "Cafe 2x"
    assert convertir("Cafe 2x", Text()) == "Cafe 2x"


# ----------------------------
# Fila completa
# ----------------------------
def test_convertir_fila_de_una_venta():
    fila = {
        "id": 7,
        "cliente_id": "3",
        "fecha": "2025-10-08 15:55:21.579917",
        "total": 1060.0,
        "pagado": 0,
        "productos_vendidos": '[{"nombre": "Split EKO 2T", "cantidad": 2.0}]',
    }
    tipos = {
        "id": Integer(), "cliente_id": Integer(), "fecha": DateTime(),
        "total": Numeric(), "pagado": Numeric(), "productos_vendidos": JSON(),
    }
    salida, problemas = convertir_fila(fila, tipos)
    assert problemas == []
    assert salida == {
        "id": 7,
        "cliente_id": 3,
        "fecha": datetime(2025, 10, 8, 15, 55, 21, 579917),
        "total": Decimal("1060.0"),
        "pagado": Decimal("0"),
        "productos_vendidos": [{"nombre": "Split EKO 2T", "cantidad": 2.0}],
    }


def test_convertir_fila_avisa_y_deja_null_lo_que_no_puede():
    """Un solo valor feo no puede voltear toda la migración."""
    fila = {"id": 1, "nombre": "Cafe", "fecha": "el jueves"}
    tipos = {"id": Integer(), "nombre": String(), "fecha": DateTime()}
    salida, problemas = convertir_fila(fila, tipos)
    assert salida["id"] == 1
    assert salida["nombre"] == "Cafe"
    assert salida["fecha"] is None
    assert len(problemas) == 1
    assert "fecha" in problemas[0]
