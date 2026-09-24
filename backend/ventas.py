# backend/ventas.py
from typing import  Dict, Optional
from datetime import datetime
from sqlalchemy import text
from backend.db import engine, ensure_datetime, ilike
from .productos import  get_product, update_product, increment_stock
from .logs import registrar_log
import copy
import json

# ------------------------------
# Registrar venta
# ----------------------------
def register_sale(cliente_id, total, pagado, usuario, tipo_pago, productos=None):
    """
    Registra una venta, descuenta del inventario,
    guarda productos vendidos como JSON y calcula saldo automáticamente.
    """

    fecha = datetime.now()
    total = float(total)
    pagado = float(pagado)
    saldo = total - pagado

    productos_data = []

    with engine.begin() as conn:

        # ----------------------------
        # Procesar productos
        # ----------------------------
        if productos:
            for item in productos:
                prod_id = item.get("id_producto") or item.get("id")
                cantidad_vendida = float(item.get("cantidad", 0))
                precio_unitario = float(item.get("precio_unitario", 0))
                subtotal = cantidad_vendida * precio_unitario

                productos_data.append({
                    "id_producto": prod_id,
                    "nombre": item.get("nombre"),
                    "cantidad": cantidad_vendida,
                    "precio_unitario": precio_unitario,
                    "subtotal": subtotal
                })

                # 🔹 Actualizar stock
                producto = get_product(prod_id)
                if producto:
                    stock_actual = float(producto.get("cantidad", 0))

                    if cantidad_vendida > stock_actual:
                        raise ValueError(
                            f"Stock insuficiente para {producto.get('nombre')}. "
                            f"Disponible: {stock_actual}"
                        )

                    nuevo_stock = stock_actual - cantidad_vendida

                    update_product(
                        prod_id,
                        nombre=producto["nombre"],
                        cantidad=nuevo_stock,
                        precio=producto["precio"]
                    )

        # ----------------------------
        # Insertar venta
        # ----------------------------
        query_venta = text("""
            INSERT INTO ventas 
            (cliente_id, total, pagado, saldo, usuario, tipo_pago, fecha, productos_vendidos)
            VALUES 
            (:cliente_id, :total, :pagado, :saldo, :usuario, :tipo_pago, :fecha, :productos_vendidos)
            RETURNING id
        """)

        venta_id = conn.execute(query_venta, {
            "cliente_id": cliente_id,
            "total": total,
            "pagado": pagado,
            "saldo": saldo,
            "usuario": usuario,
            "tipo_pago": tipo_pago,
            "fecha": fecha,
            "productos_vendidos": json.dumps(productos_data)
        }).scalar()

        # ----------------------------
        # Registrar log
        # ----------------------------
        registrar_log(
            usuario,
            "registrar_venta",
            {
                "venta_id": venta_id,
                "cliente_id": cliente_id,
                "total": total,
                "pagado": pagado,
                "saldo": saldo
            }
        )

    return {
        "id": venta_id,
        "cliente_id": cliente_id,
        "total": total,
        "pagado": pagado,
        "saldo": saldo,
        "usuario": usuario,
        "tipo_pago": tipo_pago,
        "fecha": fecha,
        "productos_vendidos": productos_data
    }


def editar_venta_extra(
    sale_id: str,
    observaciones: Optional[str] = None,
    vendedor: Optional[str] = None,
    telefono_vendedor: Optional[str] = None,
    chofer: Optional[str] = None,
    chapa: Optional[str] = None,
    usuario: Optional[str] = None
):
    with engine.begin() as conn:
        update_query = text("""
            UPDATE ventas
            SET observaciones = COALESCE(:observaciones, observaciones),
                vendedor = COALESCE(:vendedor, vendedor),
                telefono_vendedor = COALESCE(:telefono_vendedor, telefono_vendedor),
                chofer = COALESCE(:chofer, chofer),
                chapa = COALESCE(:chapa, chapa)
            WHERE id = :id
            RETURNING *
        """)

        updated = conn.execute(update_query, {
            "id": sale_id,
            "observaciones": observaciones,
            "vendedor": vendedor,
            "telefono_vendedor": telefono_vendedor,
            "chofer": chofer,
            "chapa": chapa
        }).mappings().first()

        if updated:
            registrar_log(usuario or "sistema", "editar_venta_extra", dict(updated))
            return dict(updated)

    return None


def marcar_venta_pagada(sale_id: str, usuario: Optional[str] = None) -> Optional[dict]:
    """Marca una venta como totalmente pagada (pagado = total, saldo = 0)."""
    with engine.begin() as conn:
        update_query = text("""
            UPDATE ventas
            SET pagado = total, saldo = 0
            WHERE id = :id
            RETURNING *
        """)
        updated = conn.execute(update_query, {"id": sale_id}).mappings().first()

        if updated:
            registrar_log(usuario or "sistema", "venta_pagada", {
                "venta_id": sale_id,
                "total": float(updated["total"]),
                "pagado": float(updated["pagado"])
            })
            return dict(updated)

    return None


def actualizar_factura_datos(
    sale_id: str,
    observaciones: Optional[str] = None,
    vendedor: Optional[str] = None,
    telefono_vendedor: Optional[str] = None,
    chofer: Optional[str] = None,
    chapa: Optional[str] = None,
    usuario: Optional[str] = None
):
    """Guarda los datos adicionales de la factura, siempre asignando los valores
    (permite vaciar los campos)."""
    with engine.begin() as conn:
        update_query = text("""
            UPDATE ventas
            SET observaciones = :observaciones,
                vendedor = :vendedor,
                telefono_vendedor = :telefono_vendedor,
                chofer = :chofer,
                chapa = :chapa
            WHERE id = :id
            RETURNING id
        """)

        updated = conn.execute(update_query, {
            "id": sale_id,
            "observaciones": observaciones,
            "vendedor": vendedor,
            "telefono_vendedor": telefono_vendedor,
            "chofer": chofer,
            "chapa": chapa,
        }).mappings().first()

        if updated:
            registrar_log(usuario or "sistema", "editar_venta_extra", {"id": sale_id})
            return True

    return False


# ----------------------------
# Listar ventas
# ----------------------------
def list_sales(limit=None, offset=None):
    sql = "SELECT * FROM ventas ORDER BY fecha ASC, id ASC"
    params = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    if offset is not None:
        sql += " OFFSET :offset"
        params["offset"] = offset

    query = text(sql)
    with engine.connect() as conn:
        resultados = conn.execute(query, params).mappings().all()

    ventas_list = []
    for r in resultados:
        productos_vendidos = r.get("productos_vendidos") or "[]"
        if isinstance(productos_vendidos, str):
            try:
                productos_vendidos = json.loads(productos_vendidos)
            except json.JSONDecodeError:
                productos_vendidos = []

        r_dict = dict(r)
        r_dict["productos_vendidos"] = productos_vendidos
        r_dict["fecha"] = ensure_datetime(r_dict.get("fecha"))
        ventas_list.append(r_dict)

    return ventas_list

def count_sales():
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM ventas")).scalar()

def search_sales(q: str, limit: int = 100):
    """Busca ventas por ID o nombre del cliente, devolviendo filas con cliente_nombre."""
    query = text(f"""
        SELECT v.*, COALESCE(c.nombre, 'N/A') AS cliente_nombre
        FROM ventas v
        LEFT JOIN clientes c ON c.id = v.cliente_id
        WHERE CAST(v.id AS TEXT) {ilike()} :pat OR c.nombre {ilike()} :pat
        ORDER BY v.fecha DESC
        LIMIT :limit
    """)
    with engine.connect() as conn:
        rows = [dict(r) for r in conn.execute(query, {"pat": f"%{q}%", "limit": limit}).mappings()]
    for r in rows:
        r["fecha"] = ensure_datetime(r.get("fecha"))
    return rows

def get_sale(sale_id: str) -> Optional[Dict]:
    """Devuelve una venta por su ID"""
    query = text("SELECT * FROM ventas WHERE id = :id")
    with engine.connect() as conn:
        result = conn.execute(query, {"id": sale_id}).mappings().first()

    if result:
        r = dict(result)
        r["fecha"] = ensure_datetime(r.get("fecha"))
        productos = r.get("productos_vendidos")

        # ✅ Solo hacer json.loads si es string
        if isinstance(productos, str):
            try:
                r["productos_vendidos"] = json.loads(productos)
            except json.JSONDecodeError:
                r["productos_vendidos"] = []
        else:
            # Si ya es lista/dict, dejarlo como está
            r["productos_vendidos"] = productos or []

        return r

    return None

def delete_sale(sale_id: str, usuario: Optional[str] = None) -> bool:
    """Elimina una venta por su ID y devuelve productos al stock"""
    sale = get_sale(sale_id)
    if not sale:
        return False

    # Devolver productos al stock
    for item in sale.get("productos_vendidos", []):
        increment_stock(item["id_producto"], item["cantidad"])

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM deudas WHERE venta_id = :id"), {"id": sale_id})
        conn.execute(text("DELETE FROM ventas WHERE id = :id"), {"id": sale_id})

    log_detalles = copy.deepcopy(sale)
    for key, value in log_detalles.items():
        if isinstance(value, (list, dict)):
            log_detalles[key] = json.dumps(value)

    registrar_log(usuario or "sistema", "eliminar_venta", {"venta_id": sale_id, "venta": log_detalles})

    return True

def listar_ventas_dict():
    ventas = list_sales()
    ventas_dict = {f"ID {v['id']} - Cliente {v['cliente_id']} - Total ${v['total']}": v for v in ventas}
    return ventas_dict


def exportar_ventas_dia_excel(fecha_inicio=None, fecha_fin=None) -> bytes:
    """Genera un Excel con las ventas del rango de fechas dado (inclusive)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from datetime import date as _date

    if fecha_inicio:
        fi = _date.fromisoformat(fecha_inicio[:10])
    else:
        fi = _date.today()
    if fecha_fin:
        ff = _date.fromisoformat(fecha_fin[:10])
    else:
        ff = _date.today()

    vens = list_sales() or []

    from .clientes import list_clients
    clis = {c["id"]: c["nombre"] for c in (list_clients() or [])}

    filas = []
    for v in vens:
        vfecha = v.get("fecha")
        if hasattr(vfecha, "date"):
            vd = vfecha.date()
        else:
            continue
        if not (fi <= vd <= ff):
            continue
        cliente = clis.get(v.get("cliente_id"), "Desconocido")
        estado = "Pagada" if float(v.get("pagado", 0)) >= float(v.get("total", 0)) else "Pendiente"
        for p in (v.get("productos_vendidos") or []):
            filas.append({
                "id_venta": v.get("id"),
                "fecha": vfecha.strftime("%d/%m/%Y %H:%M") if hasattr(vfecha, "strftime") else str(vfecha),
                "cliente": cliente,
                "producto": p.get("nombre", ""),
                "cantidad": int(p.get("cantidad", 0)),
                "precio_unitario": float(p.get("precio_unitario", 0)),
                "subtotal": float(p.get("subtotal", 0)),
                "total": float(v.get("total", 0)),
                "pagado": float(v.get("pagado", 0)),
                "saldo": max(float(v.get("total", 0)) - float(v.get("pagado", 0)), 0),
                "estado": estado,
            })

    wb = Workbook()
    ws = wb.active
    ws.title = "Ventas del Dia"

    headers = ["Fecha", "Cliente", "Producto", "Cantidad", "Estado"]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2E75B6")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for f in filas:
        ws.append([
            f["fecha"], f["cliente"], f["producto"],
            f["cantidad"], f["estado"],
        ])

    for col, ancho in zip("ABCDE", [20, 30, 40, 10, 12]):
        ws.column_dimensions[col].width = ancho
    for row in ws.iter_rows(min_row=2):
        row[3].number_format = '#,##0'
    ws.cell(row=ws.max_row + 1, column=3, value="TOTAL").font = Font(bold=True)
    ws.cell(row=ws.max_row, column=4, value=sum(f["cantidad"] for f in filas)).number_format = '#,##0'

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()


from io import BytesIO

def generar_factura_pdf(venta, cliente, productos_vendidos, gestor_info=None, logo_path="assets/logo.png"):
    """
    Genera una factura profesional en PDF mostrando todos los productos de la venta,
    duplicada en la misma hoja (para cliente y archivo interno).

    venta: dict con info de la venta
    cliente: dict con info del cliente
    productos_vendidos: lista de dicts {nombre, cantidad, precio_unitario}
    gestor_info: dict opcional con datos del vendedor/chofer
    logo_path: ruta local a logo de la empresa
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    import os
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=letter)
    width, height = letter
    line_height = 15

    # Cargar logo local
    logo = None
    if os.path.exists(logo_path):
        try:
            logo = ImageReader(logo_path)
        except Exception as e:
            print(f"No se pudo cargar el logo: {e}")

    def draw_multiline_text(c, text, x, y, max_width, line_height=12, font_name="Helvetica", font_size=10):
        """
        Dibuja texto ajustándolo automáticamente al ancho máximo permitido.
        Devuelve la nueva coordenada Y después de escribir el texto.
        """
        c.setFont(font_name, font_size)

        words = str(text).split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()
            if c.stringWidth(test_line, font_name, font_size) <= max_width:
                current_line = test_line
            else:
                lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        for line in lines:
            c.drawString(x, y, line)
            y -= line_height

        return y

    def dibujar_factura(y_offset=0):
        nonlocal c

        # ---------------- Logo ----------------
        if logo:
            c.drawImage(logo, 40, height - 85 - y_offset, width=80, height=70, preserveAspectRatio=True)

        # ------------- Empresa ----------------
        c.setFont("Helvetica-Bold", 14)
        c.drawString(130, height - 50 - y_offset, "Omar Galíndez Ramirez. CI: 85082506984")
        c.setFont("Helvetica", 10)
        c.drawString(130, height - 65 - y_offset, f"Factura N°: {venta.get('numero', venta.get('id',''))}")
        c.drawString(130, height - 80 - y_offset, f"Fecha: {venta.get('fecha','')}")

        # ------------- Columnas ----------------
        col1_x = 40
        col2_x = 320
        row_y = height - 110 - y_offset

        # Campos cliente, vacíos si no hay info
        cliente_nombre = cliente.get("nombre") or ""
        cliente_ci = cliente.get("ci") or ""
        cliente_chapa = cliente.get("chapa") or ""
        cliente_direccion = cliente.get("direccion") or ""
        cliente_telefono = cliente.get("telefono") or ""

        c.drawString(col1_x, row_y, f"Cliente: {cliente_nombre}"); row_y -= line_height
        c.drawString(col1_x, row_y, f"Carnet/ID: {cliente_ci}"); row_y -= line_height
        c.drawString(col1_x, row_y, f"Chapa: {cliente_chapa}"); row_y -= line_height
        row_y = draw_multiline_text(
            c,
            f"Dirección: {cliente_direccion}",
            col1_x,
            row_y,
            max_width=250,  
            line_height=line_height
        )
        c.drawString(col1_x, row_y, f"Teléfono: {cliente_telefono}"); row_y -= line_height

        # Totales y pagos
        total = float(venta.get("total") or 0)
        pagado_usd = float(venta.get("pagado") or 0)
        saldo = float(venta.get("saldo") or 0)
        metodo_pago = venta.get("tipo_pago") or ""
        vendedor = gestor_info.get("vendedor","") if gestor_info else ""
        chofer = gestor_info.get("chofer","") if gestor_info else ""
        chapa_vehiculo = gestor_info.get("chapa","") if gestor_info else ""
        observaciones = venta.get("observaciones","")

        tasa_cup = 120
        pagado_cup = pagado_usd * tasa_cup
        pagado_str = f"(USD) {pagado_usd:,.2f} - (CUP) {pagado_cup:,.2f}"

        row_y2 = height - 110 - y_offset
        c.drawString(col2_x, row_y2, f"Total: ${total:.2f}"); row_y2 -= line_height
        c.drawString(col2_x, row_y2, f"Pagado: {pagado_str}"); row_y2 -= line_height
        c.drawString(col2_x, row_y2, f"Saldo pendiente: ${saldo:.2f}"); row_y2 -= line_height
        c.drawString(col2_x, row_y2, f"Método de pago: {metodo_pago}"); row_y2 -= line_height
        if observaciones:
            row_y2 = draw_multiline_text(
                c,
                f"Observaciones: {observaciones}",
                col2_x,
                row_y2,
                max_width=220,
                line_height=line_height
            )
        c.drawString(col2_x, row_y2, f"Vendedor: {vendedor}"); row_y2 -= line_height
        c.drawString(col2_x, row_y2, f"Chofer: {chofer}"); row_y2 -= line_height
        c.drawString(col2_x, row_y2, f"Chapa: {chapa_vehiculo}"); row_y2 -= line_height

        # ------------- Tabla de productos ----------------
        table_y_start = row_y - 40
        table_data = [["Producto", "Cantidad", "Precio Unitario", "Subtotal"]]
        for p in productos_vendidos:
            nombre = p.get("nombre","")
            cantidad = float(p.get("cantidad") or 0)
            precio_unitario = float(p.get("precio_unitario") or 0)
            subtotal = cantidad * precio_unitario
            table_data.append([nombre, str(int(cantidad)), f"${precio_unitario:.2f}", f"${subtotal:.2f}"])

        table = Table(table_data, colWidths=[200, 80, 100, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,0), colors.gray),
            ('TEXTCOLOR',(0,0),(-1,0),colors.whitesmoke),
            ('ALIGN',(1,1),(-1,-1),'CENTER'),
            ('GRID', (0,0), (-1,-1), 1, colors.black),
            ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold')
        ]))
        table.wrapOn(c, 50, table_y_start - 20)
        table.drawOn(c, 50, table_y_start - len(table_data)*18 - 20)

        # ------------- Firma doble ----------------
        firma_y = table_y_start - len(table_data)*18 - 60
        c.drawString(40, firma_y -30, "__________________________")
        c.drawString(40, firma_y - 40, "Firma Cliente")
        c.drawString(320, firma_y -30 ,"__________________________")
        c.drawString(320, firma_y - 40, "Firma Vendedor")

    # Dibujar dos facturas en la misma hoja
    dibujar_factura(y_offset=0)
    c.setStrokeColor(colors.gray)
    c.setLineWidth(1)
    c.line(40, height/2, width-40, height/2)
    dibujar_factura(y_offset=height/2)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
