# Permite importar módulos desde backend
from .db import engine, MetaData

from .usuarios import (
    crear_usuario, autenticar_usuario, cambiar_password,
    requiere_cambio_password, activar_usuario, desactivar_usuario,
    obtener_logs_usuario, eliminar_usuario
)

from .productos import (
    list_products, get_product, guardar_producto,
    adjust_stock, update_product, eliminar_producto, editar_producto,
    exportar_inventario_excel
)

from .clientes import (
    list_clients, add_client, update_client,
    delete_client, get_client, edit_client,
    recalcular_deuda_cliente
)

from .ventas import (
    list_sales, get_sale, delete_sale,
    register_sale, generar_factura_pdf,
    listar_ventas_dict, editar_venta_extra,
    marcar_venta_pagada, exportar_ventas_dia_excel
)

from .deudas import (
    list_debts, get_debt, add_debt, update_debt,
    debts_by_client, delete_debt, pay_debt_producto,
    list_detalle_deudas, list_clientes_con_deuda,
    generar_factura_pago_deuda, registrar_pago,
    list_pagos, get_pago, exportar_deudas_excel,
    generar_comprobante_pago
)

from .categorias import (
    list_categories, get_category, agregar_categoria,
    editar_categoria, eliminar_categoria,
    list_products_by_category
)

from .logs import registrar_log
from .safe_db import safe_execute

from .errors import (
    AppError,
    DatabaseConnectionError,
    DatabaseQueryError,
    NotFoundError
)
