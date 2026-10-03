"""
Módulo para manejar deudas y pagos por producto en PostgreSQL.

Funciones públicas:
- list_debts()
- get_debt(deuda_id)
- add_debt(cliente_id, venta_id=None, productos=None, usuario=None)
- pay_debt_producto(deuda_id, producto_id, monto_pago, usuario=None)
- debts_by_client(cliente_id)
- delete_debt(deuda_id, usuario=None)
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from sqlalchemy import text
from .db import engine
from .clientes import update_debt, recalcular_deuda_cliente
from backend.ventas import get_sale
from backend import ventas
from backend.productos import get_product


# ======================================================
# 💾 Registrar pago en pagos_deudas
# ======================================================
def registrar_pago(
    deuda_id: int,
    detalle_id: int,
    producto_id: int,
    monto_pagado: float,
    usuario: str = None,
    metodo_pago: str = "Efectivo",
    observaciones: str = ""
) -> int:
    producto = get_product(producto_id) if producto_id else None
    detalle = None
    deuda = get_debt(deuda_id)
    if deuda:
        detalle = next((d for d in deuda.get("detalles", []) if d.get("id") == detalle_id), None)

    precio = float(detalle["precio_unitario"]) if detalle else 0
    cantidad_pagada = (float(monto_pagado) / precio) if precio else 0

    with engine.begin() as conn:
        query = text("""
            INSERT INTO pagos_deudas
            (deuda_id, detalle_id, producto_id, producto_nombre, cantidad, precio_unitario,
             monto_pagado, metodo_pago, observaciones, fecha, usuario, cliente_id)
            VALUES (:deuda_id, :detalle_id, :producto_id, :producto_nombre, :cantidad, :precio_unitario,
                    :monto_pagado, :metodo_pago, :observaciones, :fecha, :usuario, :cliente_id)
            RETURNING id
        """)
        pago_id = conn.execute(query, {
            "deuda_id": deuda_id,
            "detalle_id": detalle_id,
            "producto_id": producto_id,
            "producto_nombre": (producto or {}).get("nombre") or "",
            "cantidad": cantidad_pagada,
            "precio_unitario": precio,
            "monto_pagado": monto_pagado,
            "metodo_pago": metodo_pago,
            "observaciones": observaciones,
            "fecha": datetime.now(),
            "usuario": usuario or "desconocido",
            "cliente_id": deuda.get("cliente_id") if deuda else None,
        }).scalar()
    return pago_id


# ======================================================
# 📜 Listar pagos registrados
# ======================================================
def list_pagos(cliente_id: int = None) -> List[Dict[str, Any]]:
    if cliente_id:
        query = text("""
            SELECT p.*, c.nombre AS cliente_nombre
            FROM pagos_deudas p
            LEFT JOIN clientes c ON c.id = p.cliente_id
            WHERE p.cliente_id = :cliente_id
            ORDER BY p.fecha DESC
        """)
        params = {"cliente_id": cliente_id}
    else:
        query = text("""
            SELECT p.*, c.nombre AS cliente_nombre
            FROM pagos_deudas p
            LEFT JOIN clientes c ON c.id = p.cliente_id
            ORDER BY p.fecha DESC
        """)
        params = {}
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query, params)]


# ======================================================
# 🔍 Obtener un pago por su ID
# ======================================================
def get_pago(pago_id: int) -> Optional[Dict[str, Any]]:
    query = text("""
        SELECT p.*, c.nombre AS cliente_nombre, c.ci, c.direccion, c.telefono, c.chapa
        FROM pagos_deudas p
        LEFT JOIN clientes c ON c.id = p.cliente_id
        WHERE p.id = :pago_id
    """)
    with engine.connect() as conn:
        return conn.execute(query, {"pago_id": pago_id}).mappings().first()


# ======================================================
# 📜 Listar todas las deudas
# ======================================================
def list_debts() -> List[Dict[str, Any]]:
    query = text("SELECT * FROM deudas ORDER BY fecha DESC")
    with engine.connect() as conn:
        result = conn.execute(query)
        return [dict(row._mapping) for row in result]


# ======================================================
# 🔍 Obtener deuda con detalles
# ======================================================
def get_debt(deuda_id: int) -> Optional[Dict[str, Any]]:
    query = text("""
        SELECT d.id AS deuda_id, d.cliente_id, d.venta_id, d.monto_total, d.estado, d.fecha, d.descripcion,
               dd.id AS detalle_id, dd.producto_id, dd.cantidad, dd.precio_unitario, dd.estado AS estado_detalle
        FROM deudas d
        LEFT JOIN deudas_detalle dd ON d.id = dd.deuda_id
        WHERE d.id = :deuda_id
        ORDER BY dd.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"deuda_id": deuda_id}).mappings().all()
        if not rows:
            return None

        deuda_info = dict(rows[0])
        detalles = []
        for r in rows:
            if r["detalle_id"] is not None:
                detalles.append({
                    "id": r["detalle_id"],
                    "producto_id": r["producto_id"],
                    "cantidad": r["cantidad"],
                    "precio_unitario": r["precio_unitario"],
                    "estado": r["estado_detalle"]
                })

        deuda_info["detalles"] = detalles

        # Quitar columnas repetidas de detalle
        for k in ["detalle_id", "producto_id", "cantidad", "precio_unitario", "estado_detalle"]:
            deuda_info.pop(k, None)

        return deuda_info

# ======================================================
# ➕ Crear deuda con detalles por producto
# ======================================================

# Un centavo. Los importes vienen de division de floats, asi que comparar con
# == daria "pendiente" por un resto de 1e-13 despues de pagar la deuda entera.
TOLERANCIA = 0.01


def _repartir(monto_total: float, productos: list) -> list:
    """Reparte monto_total entre los productos de la venta.

    Devuelve [(id_producto, cantidad, precio_unitario), ...] con la misma lista
    de precios, ajustando cantidades para que la suma sea exactamente
    monto_total. El ultimo producto con precio recibe la diferencia para que no
    se pierda ni se invente un centavo.

    Los precios no se tocan: el cliente tiene que ver el precio real de cada
    producto, que es el mismo que se facturo.
    """
    filas = [(p["id_producto"], float(p["cantidad"] or 0), float(p["precio_unitario"] or 0))
             for p in productos]
    suma = sum(cant * precio for _, cant, precio in filas)

    if suma <= 0 or abs(suma - monto_total) <= TOLERANCIA:
        return filas                      # ya coincide: van las cantidades tal cual

    escala = monto_total / suma
    ultimo = max((i for i, f in enumerate(filas) if f[2] > 0), default=None)
    salida, acumulado = [], 0.0
    for i, (producto_id, cant, precio) in enumerate(filas):
        nueva = (monto_total - acumulado) / precio if i == ultimo else cant * escala
        salida.append((producto_id, nueva, precio))
        acumulado += nueva * precio

    return salida


def add_debt(
    cliente_id: int,
    venta_id: int = None,
    productos: list = None,  # lista de dicts: {id_producto, cantidad, precio_unitario}
    monto_total: float = 0.0,
    estado: str = "pendiente",
    usuario: str = None
) -> int:
    """
    Crea una deuda principal y registros por producto en deudas_detalle.

    Los detalles se ajustan para que sumen exactamente monto_total. Sin esto,
    una venta de 500 con 200 pagados dejaba una deuda de 300 cuyos productos
    seguian valiendo 500, y al pagar los 300 sobraba 200 pendiente: la deuda no
    llegaba nunca a "pagada" y el cliente seguía apareciendo como deudor.
    """
    fecha = datetime.now()
    monto_total = float(monto_total or 0)

    with engine.begin() as conn:
        # Insertar deuda principal
        query = text("""
            INSERT INTO deudas (cliente_id, venta_id, monto_total, estado, fecha, descripcion)
            VALUES (:cliente_id, :venta_id, :monto_total, :estado, :fecha, :descripcion)
            RETURNING id
        """)
        deuda_id = conn.execute(query, {
            "cliente_id": cliente_id,
            "venta_id": venta_id,
            "monto_total": monto_total,
            "estado": estado,
            "fecha": fecha,
            "descripcion": f"Deuda generada por venta {venta_id or 'N/A'}"
        }).scalar()

        # Insertar detalles por producto
        if productos:
            for producto_id, cantidad, precio in _repartir(monto_total, productos):
                conn.execute(text("""
                    INSERT INTO deudas_detalle (deuda_id, producto_id, cantidad, precio_unitario, estado)
                    VALUES (:deuda_id, :producto_id, :cantidad, :precio_unitario, 'pendiente')
                """), {
                    "deuda_id": deuda_id,
                    "producto_id": producto_id,
                    "cantidad": cantidad,
                    "precio_unitario": precio
                })

    update_debt(cliente_id, monto_total)
    return deuda_id


# ======================================================
# 💵 Registrar pago de deuda por producto
# ======================================================

def pay_debt_producto(deuda_id: int, producto_id: int, monto_pago: float, usuario=None, metodo_pago: str = "Efectivo", observaciones: str = ""):
    deuda = get_debt(deuda_id)
    if not deuda:
        raise KeyError(f"Deuda {deuda_id} no encontrada")

    detalle = next((d for d in deuda.get("detalles", []) if d.get("producto_id") == producto_id), None)
    if not detalle:
        raise KeyError(f"Producto {producto_id} no encontrado en la deuda {deuda_id}")

    precio_unitario = float(detalle["precio_unitario"])
    cantidad_pagada = monto_pago / precio_unitario
    nueva_cantidad = max(float(detalle["cantidad"]) - cantidad_pagada, 0)
    # Con floats, 741/247 puede no dar 3.0 exacto y el detalle quedaba en
    # "pendiente" con una cantidad de 1e-13. Se redondea el resto del centimo.
    if nueva_cantidad * precio_unitario <= TOLERANCIA:
        nueva_cantidad = 0
    nuevo_estado_det = "pagado" if nueva_cantidad == 0 else "pendiente"

    with engine.begin() as conn:
        # Actualizar detalle
        conn.execute(text("""
            UPDATE deudas_detalle
            SET cantidad=:cantidad, estado=:estado
            WHERE id=:id
        """), {"cantidad": nueva_cantidad, "estado": nuevo_estado_det, "id": detalle["id"]})

        # Actualizar estado de deuda principal
        #
        # Se decide por plata, no solo por la suma de los detalles: una deuda
        # vieja puede tener los detalles desactualizados y, si se pagara de mas,
        # la suma volveria a dar positivo y reabriria una deuda ya saldada.
        # El pago actual todavia no esta en pagos_deudas (se registra al final),
        # asi que se suma aparte.
        total_restante = conn.execute(text("""
            SELECT SUM(cantidad * precio_unitario)
            FROM deudas_detalle
            WHERE deuda_id=:deuda_id AND estado='pendiente'
        """), {"deuda_id": deuda_id}).scalar() or 0

        ya_pagado = conn.execute(text("""
            SELECT COALESCE(SUM(monto_pagado), 0)
            FROM pagos_deudas
            WHERE deuda_id=:deuda_id
        """), {"deuda_id": deuda_id}).scalar() or 0

        monto_deuda = float(deuda.get("monto_total") or 0)
        pagado_total = float(ya_pagado) + float(monto_pago)

        saldo_cero = (float(total_restante) <= TOLERANCIA
                      or pagado_total >= monto_deuda - TOLERANCIA)
        estado_deuda = "pagada" if saldo_cero else "pendiente"
        conn.execute(text("""
            UPDATE deudas
            SET estado=:estado
            WHERE id=:deuda_id
        """), {"estado": estado_deuda, "deuda_id": deuda_id})

        # Actualizar venta asociada
        if saldo_cero:
            deuda["estado"] = "pagada"
            venta_id = deuda.get("venta_id")
            if venta_id:
                ventas.marcar_venta_pagada(sale_id=venta_id, usuario=usuario)

    # Fuera de la transacción: recalcular deuda_total del cliente sincronizada
    # con sus deudas pendientes (el estado del detalle ya está confirmado).
    recalcular_deuda_cliente(deuda["cliente_id"], usuario=usuario)

    pago_id = registrar_pago(
        deuda_id=deuda_id,
        detalle_id=detalle["id"],
        producto_id=producto_id,
        monto_pagado=monto_pago,
        usuario=usuario,
        metodo_pago=metodo_pago,
        observaciones=observaciones,
    )

    return {"detalle": detalle, "estado_deuda": estado_deuda, "pago_id": pago_id}


# ======================================================
# 📋 Listar deudas por cliente
# ======================================================
def debts_by_client(cliente_id: int):
    query = text("""
        SELECT d.id AS deuda_id, d.cliente_id, d.venta_id, d.monto_total, d.estado, d.fecha, d.descripcion,
               dd.id AS detalle_id, dd.producto_id, dd.cantidad, dd.precio_unitario, dd.estado AS estado_detalle
        FROM deudas d
        LEFT JOIN deudas_detalle dd ON d.id = dd.deuda_id
        WHERE d.cliente_id = :cliente_id
        ORDER BY d.fecha DESC, dd.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"cliente_id": cliente_id}).mappings().all()
        if not rows:
            return []

        deudas_map = {}
        for r in rows:
            d_id = r["deuda_id"]
            if d_id not in deudas_map:
                deudas_map[d_id] = dict(r)
                deudas_map[d_id]["detalles"] = []

            if r["detalle_id"] is not None:
                deudas_map[d_id]["detalles"].append({
                    "id": r["detalle_id"],
                    "producto_id": r["producto_id"],
                    "cantidad": r["cantidad"],
                    "precio_unitario": r["precio_unitario"],
                    "estado": r["estado_detalle"]
                })

        # Limpiar columnas repetidas de detalle
        for deuda in deudas_map.values():
            for k in ["detalle_id", "producto_id", "cantidad", "precio_unitario", "estado_detalle"]:
                deuda.pop(k, None)

        return list(deudas_map.values())

def pendientes_por_cliente(cliente_id: int):
    """Deudas pendientes del cliente, con el nombre de cada producto.

    Para el modal de deudas de /ventas: ahi solo interesa lo que falta pagar,
    asi que las deudas ya pagadas se dejan fuera, y la descripcion se arma con
    producto y cantidad, que es lo que el usuario necesita ver.

    debts_by_client se deja como estaba porque el modulo /deudas si muestra
    el historial completo.
    """
    query = text("""
        SELECT d.id AS deuda_id, d.venta_id, d.monto_total, d.estado, d.fecha,
               dd.id AS detalle_id, dd.producto_id, dd.cantidad,
               dd.precio_unitario, dd.estado AS estado_detalle,
               p.nombre AS producto_nombre
        FROM deudas d
        LEFT JOIN deudas_detalle dd ON dd.deuda_id = d.id
        LEFT JOIN productos p ON p.id = dd.producto_id
        WHERE d.cliente_id = :cliente_id
          AND COALESCE(d.estado, 'pendiente') <> 'pagada'
        ORDER BY d.fecha DESC, dd.id
    """)
    with engine.connect() as conn:
        rows = conn.execute(query, {"cliente_id": cliente_id}).mappings().all()

    agrupadas = {}
    for r in rows:
        d_id = r["deuda_id"]
        if d_id not in agrupadas:
            agrupadas[d_id] = {
                "deuda_id": d_id,
                "venta_id": r["venta_id"],
                "monto_total": r["monto_total"],
                "estado": r["estado"],
                "fecha": r["fecha"],
                "detalles": [],
            }
        if r["detalle_id"] is not None:
            if r["producto_nombre"]:
                nombre = r["producto_nombre"]
            elif r["producto_id"] is not None:
                nombre = "Producto eliminado"      # se borro del catalogo
            else:
                nombre = "Sin producto"
            agrupadas[d_id]["detalles"].append({
                "id": r["detalle_id"],
                "producto": nombre,
                "cantidad": r["cantidad"],
                "precio_unitario": r["precio_unitario"],
                "estado": r["estado_detalle"],
            })

    return list(agrupadas.values())

# ======================================================
# 🗑️ Eliminar deuda
# ======================================================
def delete_debt(deuda_id: int, usuario: Optional[str] = None) -> bool:
    deuda = get_debt(deuda_id)
    if not deuda:
        return False

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM deudas_detalle WHERE deuda_id=:id"), {"id": deuda_id})
        conn.execute(text("DELETE FROM deudas WHERE id=:id"), {"id": deuda_id})

    update_debt(deuda["cliente_id"], -float(deuda["monto_total"]))

    try:
        from .logs import registrar_log
        registrar_log(usuario or "sistema", "eliminar_deuda", {
            "deuda_id": deuda_id,
            "cliente_id": deuda["cliente_id"],
            "monto_total": deuda["monto_total"]
        })
    except Exception:
        pass

    return True


# ======================================================
# 📊 Listar todos los detalles de deudas
# ======================================================
def list_detalle_deudas():
    query = text("""
        SELECT dd.id AS detalle_id, dd.deuda_id, dd.producto_id, dd.cantidad, dd.precio_unitario, dd.estado,
               d.cliente_id, d.fecha, d.monto_total, d.estado AS estado_deuda
        FROM deudas_detalle dd
        JOIN deudas d ON d.id = dd.deuda_id
        ORDER BY d.fecha DESC
    """)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(query)]


# ======================================================
# 📋 Listar clientes con deudas pendientes
# ======================================================
def list_clientes_con_deuda():
    """Clientes que deben algo, con el monto que REALMENTE deben.

    El importe sale de los detalles pendientes, no de clientes.deuda_total.
    Esa columna queda desactualizada con facilidad (deudas viejas, pagos
    parciales, deudas de ventas ya saldadas) y filtrar por ella era lo que
    dejaba clientes con deuda fuera del selector de /deudas.
    """
    query = text("""
        SELECT c.id, c.nombre, c.telefono, c.ci, c.chapa,
               COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) AS deuda_total
        FROM clientes c
        JOIN deudas d ON d.cliente_id = c.id AND d.estado = 'pendiente'
        JOIN deudas_detalle dd ON dd.deuda_id = d.id AND dd.estado = 'pendiente'
        GROUP BY c.id, c.nombre, c.telefono, c.ci, c.chapa
        HAVING COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) > :tolerancia
        ORDER BY c.nombre
    """)
    with engine.connect() as conn:
        return [dict(row._mapping) for row in
                conn.execute(query, {"tolerancia": TOLERANCIA})]


def deuda_pendiente_por_cliente() -> Dict[int, float]:
    """Importe pendiente real de cada cliente, para completar el buscador.

    El buscador de /deudas busca entre TODOS los clientes, igual que el de
    /clientes, asi que necesita saber cuanto debe cada uno, no solo los que
    aparecen en list_clientes_con_deuda.
    """
    query = text("""
        SELECT d.cliente_id,
               COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0) AS total
        FROM deudas d
        JOIN deudas_detalle dd ON dd.deuda_id = d.id AND dd.estado = 'pendiente'
        WHERE d.estado = 'pendiente'
        GROUP BY d.cliente_id
    """)
    with engine.connect() as conn:
        return {int(r.cliente_id): float(r.total or 0) for r in conn.execute(query)}


from io import BytesIO

# Contador simple global para número de deuda
DEUDA_COUNTER = 1

def generar_factura_pago_deuda(
    cliente,
    productos_pagados,
    deuda_id=None,
    usuario="desconocido",
    metodo_pago="Efectivo",
    observaciones="",
    logo_path=None
):
    """
    Genera un PDF de factura de pago de deuda para un cliente,
    duplicada en la misma hoja (una copia para el cliente y otra para archivo interno).
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    from reportlab.lib.utils import ImageReader
    import os
    from datetime import datetime
    global DEUDA_COUNTER
    if logo_path is None:
        logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.png")
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    line_height = 15

    # ---------------- Logo ----------------
    logo = None
    if os.path.exists(logo_path):
        try:
            logo = ImageReader(logo_path)
        except Exception as e:
            print(f"No se pudo cargar el logo: {e}")

    # Generar número de deuda consecutivo si no se pasa
    if deuda_id is None:
        deuda_id = DEUDA_COUNTER
        DEUDA_COUNTER += 1

    # Fecha de pago actual
    fecha_pago = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    def draw_factura(y_offset=0):
        nonlocal c
        current_y = height - 50 - y_offset

        # Logo
        if logo:
            c.drawImage(logo, 40, current_y - 30, width=80, height=60, preserveAspectRatio=True)

        # Título
        c.setFont("Helvetica-Bold", 14)
        c.drawString(150, current_y, "COMPROBANTE DE PAGO DE DEUDA")

        # Info deuda
        c.setFont("Helvetica", 10)
        current_y -= 40
        c.drawString(50, current_y, f"No. de Deuda: {str(deuda_id)}")
        current_y -= line_height
        c.drawString(50, current_y, f"Fecha de Pago: {fecha_pago}")
        current_y -= line_height

        # Info cliente
        c.setFont("Helvetica-Bold", 12)
        c.drawString(50, current_y, "Datos del Cliente:")
        current_y -= line_height
        c.setFont("Helvetica", 10)
        c.drawString(50, current_y, f"Nombre: {cliente.get('nombre','N/A')}"); current_y -= line_height
        c.drawString(50, current_y, f"CI / Documento: {cliente.get('ci','N/A')}"); current_y -= line_height
        c.drawString(50, current_y, f"Dirección: {cliente.get('direccion','N/A')}"); current_y -= line_height
        c.drawString(50, current_y, f"Teléfono: {cliente.get('telefono','N/A')}"); current_y -= line_height
        c.drawString(50, current_y, f"Chapa: {cliente.get('chapa','N/A')}"); current_y -= line_height + 10

        # Tabla productos
        table_y = current_y
        table_data = [["Producto", "Cantidad", "Precio Unitario", "Monto Pagado"]]
        total_pagado = 0
        for p in productos_pagados:
            cantidad = float(p.get("cantidad") or 0)
            precio = float(p.get("precio_unitario") or 0)
            subtotal = cantidad * precio
            total_pagado += subtotal
            table_data.append([p.get("nombre",""), str(int(cantidad)), f"${precio:.2f}", f"${subtotal:.2f}"])

        table = Table(table_data, colWidths=[200, 80, 100, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.gray),
            ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
            ('ALIGN',(1,1),(-1,-1),'CENTER'),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
        ]))
        table.wrapOn(c, 50, table_y - 20)
        table.drawOn(c, 50, table_y - len(table_data)*18)

        # Totales
        c.drawString(50, table_y - len(table_data)*18 - 20, f"Total Pagado: ${total_pagado:.2f}")
        c.drawString(50, table_y - len(table_data)*18 - 40, f"Método de pago: {metodo_pago or 'N/A'}")
        if observaciones:
            c.drawString(50, table_y - len(table_data)*18 - 60, f"Observaciones: {observaciones}")

        # Firmas
        firma_y = table_y - len(table_data)*18 - 100
        c.drawString(50, firma_y, "__________________________")
        c.drawString(50, firma_y - 12, "Firma Cliente")
        c.drawString(320, firma_y, "__________________________")
        c.drawString(320, firma_y - 12, "Firma Vendedor / Responsable")

    # ---------------- Dibujar dos facturas en la misma hoja ----------------
    draw_factura(y_offset=0)
    c.setStrokeColor(colors.gray)
    c.setLineWidth(1)
    c.line(40, height/2, width-40, height/2)  # línea divisoria
    draw_factura(y_offset=height/2)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()


# ======================================================
# 📥 Exportar deudas a Excel
# ======================================================
def exportar_deudas_excel(cliente_id: int = None) -> bytes:
    """Genera un archivo Excel con las deudas pendientes.
    Si se pasa cliente_id, solo las deudas de ese cliente."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment

    nombres = {}
    from .clientes import list_clients
    for c in list_clients() or []:
        nombres[c["id"]] = c["nombre"]

    prod_map = {}
    from .productos import map_productos
    prod_map = map_productos() or {}

    rollos = list_detalle_deudas()
    filas = []
    for d in rollos:
        if str(d.get("estado", "pendiente")).lower() != "pendiente":
            continue
        if cliente_id and d.get("cliente_id") != cliente_id:
            continue
        cant = float(d.get("cantidad") or 0)
        precio = float(d.get("precio_unitario") or 0)
        filas.append({
            "cliente": nombres.get(d.get("cliente_id"), "Desconocido"),
            "deuda_id": d.get("deuda_id"),
            "detalle_id": d.get("detalle_id"),
            "producto": prod_map.get(d.get("producto_id"), "Producto"),
            "cantidad": cant,
            "precio": precio,
            "monto": round(cant * precio, 2),
            "fecha": str(d.get("fecha", ""))[:19],
        })

    wb = Workbook()
    ws = wb.active
    ws.title = "Deudas Pendientes"

    headers = ["Cliente", "Producto", "Cantidad", "Fecha"]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="C0392B")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for f in filas:
        ws.append([
            f["cliente"], f["producto"], f["cantidad"], f["fecha"]
        ])

    for col, ancho in zip("ABCD", [30, 40, 12, 24]):
        ws.column_dimensions[col].width = ancho

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


# ======================================================
# 🧾 Generar comprobante de pago a partir de un pago
# ======================================================
def generar_comprobante_pago(pago_id: int, usuario: str = "desconocido") -> Optional[bytes]:
    """Genera el PDF comprobante de pago usando un registro de pagos_deudas."""
    from openpyxl import Workbook  # noqa (evitar import circular con clientes)
    from .clientes import get_client

    pago = get_pago(pago_id)
    if not pago:
        return None

    cliente = get_client(pago.get("cliente_id")) or {}
    productos_pagados = [{
        "nombre": pago.get("producto_nombre") or "Producto",
        "cantidad": float(pago.get("cantidad") or 0),
        "precio_unitario": float(pago.get("precio_unitario") or 0),
    }]

    return generar_factura_pago_deuda(
        cliente=cliente,
        productos_pagados=productos_pagados,
        deuda_id=pago.get("deuda_id"),
        usuario=usuario,
        metodo_pago=pago.get("metodo_pago") or "Efectivo",
        observaciones=pago.get("observaciones") or "",
    )
