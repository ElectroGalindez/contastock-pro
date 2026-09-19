"""
Tests del flujo de deudas: creación, pago por producto,
registro de pagos y transición de la venta pendiente -> pagada.
"""
import pytest
from backend.ventas import register_sale, get_sale
from backend.deudas import (
    add_debt, get_debt, pay_debt_producto, list_debts, list_pagos
)
from backend.productos import get_product
from backend.clientes import get_client


def _crear_scenario(cliente, producto, cantidad=2, precio=100, pagado=0):
    venta = register_sale(
        cliente_id=cliente["id"], total=cantidad * precio, pagado=pagado,
        usuario="test", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": cantidad, "precio_unitario": precio}],
    )
    deuda_id = add_debt(
        cliente_id=cliente["id"], monto_total=venta["saldo"], venta_id=venta["id"],
        productos=[{"id_producto": producto["id"], "cantidad": cantidad, "precio_unitario": precio}],
        usuario="test",
    )
    return venta, deuda_id


def test_pagar_deuda_completa_marca_venta_pagada(cliente, producto):
    venta, deuda_id = _crear_scenario(cliente, producto, cantidad=2, precio=100, pagado=0)
    assert get_sale(venta["id"])["pagado"] == 0

    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 200.0, "test", "Efectivo")

    assert res["estado_deuda"] == "pagada"
    assert get_debt(deuda_id)["estado"] == "pagada"
    # La venta debe quedar pagada en BD
    guardada = get_sale(venta["id"])
    assert float(guardada["pagado"]) == 200.0
    assert float(guardada["saldo"]) == 0.0


def test_pago_parcial_no_marca_venta_pagada(cliente, producto):
    venta, deuda_id = _crear_scenario(cliente, producto, cantidad=2, precio=100, pagado=0)

    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 100.0, "test", "Efectivo")

    assert res["estado_deuda"] == "pendiente"
    # El pago parcial no marca la venta como pagada
    assert float(get_sale(venta["id"])["pagado"]) == 0.0


def test_pago_registra_en_pagos_deudas(cliente, producto):
    venta, deuda_id = _crear_scenario(cliente, producto, cantidad=2, precio=100, pagado=0)

    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 200.0, "test", "Efectivo")

    pagos = list_pagos(cliente_id=cliente["id"])
    assert len(pagos) == 1
    pago = pagos[0]
    assert pago["deuda_id"] == deuda_id
    assert float(pago["monto_pagado"]) == 200.0
    assert pago["producto_id"] == detalle["producto_id"]


def test_deuda_inexistente_levanta_error(cliente, producto):
    with pytest.raises(KeyError):
        pay_debt_producto(99999, 1, 100.0, "test", "Efectivo")


def test_pago_con_metodo_zelle(cliente, producto):
    venta, deuda_id = _crear_scenario(cliente, producto, cantidad=1, precio=100, pagado=0)

    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 100.0, "test", "Zelle")

    pagos = list_pagos(cliente_id=cliente["id"])
    assert pagos[0]["metodo_pago"] == "Zelle"


def test_deuda_total_cliente_se_sincroniza(cliente, producto):
    # Al crear la deuda, deuda_total del cliente sube
    venta, deuda_id = _crear_scenario(cliente, producto, cantidad=2, precio=100, pagado=0)
    assert float(get_client(cliente["id"])["deuda_total"]) == 200.0

    # Pago parcial: baja a 100
    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    pay_debt_producto(deuda_id, detalle["producto_id"], 100.0, "test", "Efectivo")
    assert float(get_client(cliente["id"])["deuda_total"]) == 100.0

    # Pago completo: baja a 0
    deuda = get_debt(deuda_id)
    detalle = deuda["detalles"][0]
    pay_debt_producto(deuda_id, detalle["producto_id"], 100.0, "test", "Efectivo")
    assert float(get_client(cliente["id"])["deuda_total"]) == 0.0