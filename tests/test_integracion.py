"""
Test de integración: flujo completo de negocio
venta -> deuda -> pago -> venta pagada -> (reposición si se elimina).
"""
import pytest
from backend.ventas import register_sale, get_sale, delete_sale, marcar_venta_pagada
from backend.deudas import add_debt, get_debt, pay_debt_producto, list_debts
from backend.productos import get_product


def test_flujo_completo_pendiente_a_pagada(cliente, producto):
    stock_inicial = get_product(producto["id"])["cantidad"]

    # 1. Registrar venta pendiente
    venta = register_sale(
        cliente_id=cliente["id"], total=600, pagado=0,
        usuario="Omar", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 6, "precio_unitario": 100}],
    )
    assert get_product(producto["id"])["cantidad"] == stock_inicial - 6
    assert float(venta["saldo"]) == 600

    # 2. Crear deuda asociada
    deuda_id = add_debt(
        cliente_id=cliente["id"], monto_total=600, venta_id=venta["id"],
        productos=[{"id_producto": producto["id"], "cantidad": 6, "precio_unitario": 100}],
        usuario="Omar",
    )

    # 3. La venta NO está pagada todavía
    assert float(get_sale(venta["id"])["pagado"]) == 0

    # 4. Cobrar la deuda completa
    detalle = get_debt(deuda_id)["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 600.0, "Omar", "Efectivo")
    assert res["estado_deuda"] == "pagada"

    # 5. La venta pasó a pagada automáticamente
    venta_final = get_sale(venta["id"])
    assert float(venta_final["pagado"]) == 600.0
    assert float(venta_final["saldo"]) == 0.0

    # 6. Stock correto
    assert get_product(producto["id"])["cantidad"] == stock_inicial - 6

    # 7. Si se elimina la venta (error de captura), se repone el stock
    delete_sale(venta["id"], "Omar")
    assert get_product(producto["id"])["cantidad"] == stock_inicial
    assert get_sale(venta["id"]) is None


def test_flujo_pago_parcial_luego_total(cliente, producto):
    venta = register_sale(
        cliente_id=cliente["id"], total=400, pagado=0,
        usuario="test", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 4, "precio_unitario": 100}],
    )
    deuda_id = add_debt(
        cliente_id=cliente["id"], monto_total=400, venta_id=venta["id"],
        productos=[{"id_producto": producto["id"], "cantidad": 4, "precio_unitario": 100}],
        usuario="test",
    )

    # Pago parcial
    detalle = get_debt(deuda_id)["detalles"][0]
    res = pay_debt_producto(deuda_id, detalle["producto_id"], 150.0, "test", "Efectivo")
    assert res["estado_deuda"] == "pendiente"
    assert float(get_sale(venta["id"])["pagado"]) == 0.0

    detalle = get_debt(deuda_id)["detalles"][0]  # recargar para ver cantidad restante
    restante = float(detalle["cantidad"]) * float(detalle["precio_unitario"])
    assert restante == 250.0

    # Pago del resto
    res = pay_debt_producto(deuda_id, detalle["producto_id"], restante, "test", "Efectivo")
    assert res["estado_deuda"] == "pagada"
    assert float(get_sale(venta["id"])["pagado"]) == 400.0

    # Limpiar
    delete_sale(venta["id"], "test")


def test_marcar_venta_pagada_no_existe(cliente, producto):
    """No debe crashear al marcar una venta inexistente y devolver None."""
    assert marcar_venta_pagada(999999, "test") is None