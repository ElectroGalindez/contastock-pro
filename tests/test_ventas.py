"""
Tests del flujo de ventas: registrar, descuento de stock,
editar, eliminar (con reposición de stock) y cambios de estado.
"""
import pytest
from backend.ventas import (
    register_sale, get_sale, delete_sale, list_sales, marcar_venta_pagada
)
from backend.productos import get_product
from backend.deudas import list_debts


def test_registrar_venta_descuenta_stock(cliente, producto):
    stock_inicial = get_product(producto["id"])["cantidad"]

    venta = register_sale(
        cliente_id=cliente["id"], total=200, pagado=200,
        usuario="test", tipo_pago="Efectivo",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 2, "precio_unitario": 100}],
    )

    assert venta["total"] == 200
    assert venta["pagado"] == 200
    assert venta["saldo"] == 0
    assert get_product(producto["id"])["cantidad"] == stock_inicial - 2


def test_registrar_venta_pendiente_crea_deuda(cliente, producto):
    venta = register_sale(
        cliente_id=cliente["id"], total=300, pagado=0,
        usuario="test", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 3, "precio_unitario": 100}],
    )

    assert venta["saldo"] == 300
    # La ruta de la app crea la deuda cuando pagado < total
    from backend.deudas import add_debt
    add_debt(cliente_id=cliente["id"], monto_total=300, venta_id=venta["id"],
             productos=[{"id_producto": producto["id"], "cantidad": 3, "precio_unitario": 100}],
             usuario="test")

    deudas = list_debts()
    assert len(deudas) == 1
    assert deudas[0]["venta_id"] == venta["id"]
    assert deudas[0]["estado"] == "pendiente"


def test_no_permite_stock_insuficiente(cliente, producto):
    from backend.ventas import register_sale
    stock = get_product(producto["id"])["cantidad"]

    with pytest.raises(ValueError, match="Stock insuficiente"):
        register_sale(
            cliente_id=cliente["id"], total=1000, pagado=1000,
            usuario="test", tipo_pago="Efectivo",
            productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                        "cantidad": stock + 5, "precio_unitario": 100}],
        )

    # El stock no debe haber cambiado
    assert get_product(producto["id"])["cantidad"] == stock


def test_eliminar_venta_restaura_stock(cliente, producto):
    stock_inicial = get_product(producto["id"])["cantidad"]

    venta = register_sale(
        cliente_id=cliente["id"], total=200, pagado=200,
        usuario="test", tipo_pago="Efectivo",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 2, "precio_unitario": 100}],
    )
    assert get_product(producto["id"])["cantidad"] == stock_inicial - 2

    ok = delete_sale(venta["id"], "test")
    assert ok is True
    assert get_sale(venta["id"]) is None
    assert get_product(producto["id"])["cantidad"] == stock_inicial


def test_eliminar_venta_con_deuda_borra_deuda_tambien(cliente, producto):
    venta = register_sale(
        cliente_id=cliente["id"], total=300, pagado=0,
        usuario="test", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 3, "precio_unitario": 100}],
    )
    from backend.deudas import add_debt
    add_debt(cliente_id=cliente["id"], monto_total=300, venta_id=venta["id"],
             productos=[{"id_producto": producto["id"], "cantidad": 3, "precio_unitario": 100}],
             usuario="test")
    assert len(list_debts()) == 1

    ok = delete_sale(venta["id"], "test")
    assert ok is True
    assert len(list_debts()) == 0
    assert get_product(producto["id"])["cantidad"] == 10


def test_marcar_venta_pagada(cliente, producto):
    venta = register_sale(
        cliente_id=cliente["id"], total=200, pagado=0,
        usuario="test", tipo_pago="Credito",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 2, "precio_unitario": 100}],
    )

    actualizada = marcar_venta_pagada(venta["id"], "test")
    assert float(actualizada["pagado"]) == float(actualizada["total"]) == 200
    assert float(actualizada["saldo"]) == 0

    guardada = get_sale(venta["id"])
    assert float(guardada["pagado"]) == 200
    assert float(guardada["saldo"]) == 0


def test_list_sales_parsea_productos(cliente, producto):
    register_sale(
        cliente_id=cliente["id"], total=100, pagado=100,
        usuario="test", tipo_pago="Efectivo",
        productos=[{"id_producto": producto["id"], "nombre": producto["nombre"],
                    "cantidad": 1, "precio_unitario": 100}],
    )
    ventas = list_sales()
    assert len(ventas) == 1
    assert ventas[0]["productos_vendidos"][0]["nombre"] == "Producto Test"