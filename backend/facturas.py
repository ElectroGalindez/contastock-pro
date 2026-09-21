from io import BytesIO
import os
import json

from . import ventas, clientes
from .config import resource_path


def generar_factura_pdf(venta, cliente, productos_vendidos, gestor_info=None, logo_path=None):
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
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Table, TableStyle
    from reportlab.pdfgen import canvas as canvas_module
    from reportlab.lib import colors as _colors
    buffer = BytesIO()
    c = canvas_module.Canvas(buffer, pagesize=letter)
    width, height = letter
    line_height = 15

    if logo_path is None:
        logo_path = str(resource_path("assets/logo.png"))

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
        vendedor = gestor_info.get("vendedor", "") if gestor_info else ""
        chofer = gestor_info.get("chofer", "") if gestor_info else ""
        chapa_vehiculo = gestor_info.get("chapa", "") if gestor_info else ""
        observaciones = venta.get("observaciones", "")

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
            nombre = p.get("nombre", "")
            cantidad = float(p.get("cantidad") or 0)
            precio_unitario = float(p.get("precio_unitario") or 0)
            subtotal = cantidad * precio_unitario
            table_data.append([nombre, str(int(cantidad)), f"${precio_unitario:.2f}", f"${subtotal:.2f}"])

        table = Table(table_data, colWidths=[200, 80, 100, 100])
        table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), _colors.gray),
            ('TEXTCOLOR', (0, 0), (-1, 0), _colors.whitesmoke),
            ('ALIGN', (1, 1), (-1, -1), 'CENTER'),
            ('GRID', (0, 0), (-1, -1), 1, _colors.black),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold')
        ]))
        table.wrapOn(c, 50, table_y_start - 20)
        table.drawOn(c, 50, table_y_start - len(table_data) * 18 - 20)

        # ------------- Firma doble ----------------
        firma_y = table_y_start - len(table_data) * 18 - 60
        c.drawString(40, firma_y - 30, "__________________________")
        c.drawString(40, firma_y - 40, "Firma Cliente")
        c.drawString(320, firma_y - 30, "__________________________")
        c.drawString(320, firma_y - 40, "Firma Vendedor")

    # Dibujar dos facturas en la misma hoja
    dibujar_factura(y_offset=0)
    c.setStrokeColor(_colors.gray)
    c.setLineWidth(1)
    c.line(40, height / 2, width - 40, height / 2)
    dibujar_factura(y_offset=height / 2)

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer


def generar_factura(venta_id, usuario=""):
    """Genera la factura PDF de una venta. Devuelve un BytesIO."""
    v = ventas.get_sale(venta_id)
    if not v:
        return None

    cli = clientes.get_client(v["cliente_id"]) or {}

    productos_vendidos = v.get("productos_vendidos") or []
    if isinstance(productos_vendidos, str):
        try:
            productos_vendidos = json.loads(productos_vendidos)
        except Exception:
            productos_vendidos = []

    vendedor = v.get("vendedor") or v.get("usuario") or usuario
    telefono_vendedor = v.get("telefono_vendedor") or ""
    if vendedor and telefono_vendedor:
        tel_formateado = str(telefono_vendedor).strip()
        if not tel_formateado.startswith("+"):
            tel_formateado = f"+53 {tel_formateado}" if not tel_formateado.startswith("53") else f"+{tel_formateado}"
        vendedor = f"{vendedor} ({tel_formateado})"

    gestor_info = {
        "vendedor": vendedor,
        "chofer": v.get("chofer") or "",
        "chapa": v.get("chapa") or "",
    }

    logo_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "logo.png")

    return generar_factura_pdf(
        venta=v,
        cliente=cli,
        productos_vendidos=productos_vendidos,
        gestor_info=gestor_info,
        logo_path=logo_path,
    )