# backend/productos.py
from typing import List, Dict, Any
from sqlalchemy import text
from .db import engine
from .logs import registrar_log
from typing import Optional


# ---------------------------
# LISTAR PRODUCTOS
# ---------------------------
def list_products() -> List[Dict[str, Any]]:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM productos ORDER BY nombre"))
        return [dict(r._mapping) for r in result]

def map_productos() -> Dict[str, str]:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, nombre FROM productos"))
        return {row["id"]: row["nombre"] for row in result.mappings()}
# ---------------------------
# AGREGAR PRODUCTO
# ---------------------------
def guardar_producto(
    nombre: str,
    precio: float,
    cantidad: int,
    categoria_id: str,
    usuario: Optional[str] = None
) -> dict:
    """
    Crea un nuevo producto o edita uno existente si ya existe.
    Devuelve el producto creado/actualizado como diccionario.
    """
    nombre = nombre.strip()
    if not nombre:
        raise ValueError("El nombre del producto no puede estar vacío.")

    with engine.begin() as conn:
        # Verificar si ya existe un producto con ese nombre
        select_query = text("SELECT * FROM productos WHERE nombre = :nombre")
        existing = conn.execute(select_query, {"nombre": nombre}).mappings().fetchone()

        if existing:
            # Editar producto existente
            update_query = text("""
                UPDATE productos
                SET precio = :precio,
                    cantidad = :cantidad,
                    categoria_id = :categoria_id
                WHERE id = :id
                RETURNING *
            """)
            updated = conn.execute(update_query, {
                "precio": precio,
                "cantidad": cantidad,
                "categoria_id": categoria_id,
                "id": existing["id"]
            }).mappings().fetchone()

            registrar_log(usuario or "sistema", "editar_producto", {
                "id": updated["id"],
                "nombre": nombre,
                "precio": precio,
                "cantidad": cantidad,
                "categoria_id": categoria_id
            })

            return dict(updated)

        else:
            # Crear nuevo producto
            insert_query = text("""
                INSERT INTO productos (nombre, precio, cantidad, categoria_id)
                VALUES (:nombre, :precio, :cantidad, :categoria_id)
                RETURNING *
            """)
            new_prod = conn.execute(insert_query, {
                "nombre": nombre,
                "precio": precio,
                "cantidad": cantidad,
                "categoria_id": categoria_id
            }).mappings().fetchone()

            registrar_log(usuario or "sistema", "crear_producto", {
                "id": new_prod["id"],
                "nombre": nombre,
                "precio": precio,
                "cantidad": cantidad,
                "categoria_id": categoria_id
            })

            return dict(new_prod)
        
# editar producto
def editar_producto(
    producto_id: str,
    nombre: str,
    precio: float,
    cantidad: int,
    categoria_id: str,
    usuario: Optional[str] = None
) -> dict:  
    with engine.begin() as conn:
        update_query = text("""
            UPDATE productos
            SET nombre = :nombre,
                precio = :precio,
                cantidad = :cantidad,
                categoria_id = :categoria_id
            WHERE id = :id
            RETURNING *
        """)
        updated = conn.execute(update_query, {
            "nombre": nombre,
            "precio": precio,
            "cantidad": cantidad,
            "categoria_id": categoria_id,
            "id": producto_id
        }).mappings().fetchone()

        registrar_log(usuario or "sistema", "editar_producto", {
            "id": updated["id"],
            "nombre": nombre,
            "precio": precio,
            "cantidad": cantidad,
            "categoria_id": categoria_id
        })

        return dict(updated)    
    



# ---------------------------
# OBTENER PRODUCTO
# ---------------------------
def get_product(producto_id: str) -> Dict[str, Any]:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT * FROM productos WHERE id = :id"), {"id": producto_id})
        row = result.first()
        return dict(row._mapping) if row else None

# ---------------------------
# ELIMINAR PRODUCTO
# ---------------------------
def delete_product(producto_id: str, usuario: Optional[str] = None) -> bool:
    with engine.begin() as conn:
        # Obtener el nombre del producto antes de eliminarlo para el log
        result = conn.execute(text("SELECT nombre FROM productos WHERE id = :id"), {"id": producto_id})
        row = result.mappings().first()
        if not row:
            return False  # Producto no encontrado

        producto_nombre = row["nombre"]

        # Eliminar el producto
        conn.execute(text("DELETE FROM productos WHERE id = :id"), {"id": producto_id})

        # Registrar log de eliminación
        if usuario:
            registrar_log(usuario, "eliminar_producto", {
                "id": producto_id,
                "nombre": producto_nombre
            })

        return True

# ---------------------------
#   adjust_stock
# ---------------------------

def adjust_stock(product_id: str, cantidad_delta: int, usuario=None) -> dict:
    """
    Ajusta el stock de un producto sumando o restando cantidad_delta.
    Devuelve el producto actualizado.
    """
    with engine.begin() as conn:
        # Obtener stock actual
        result = conn.execute(text("SELECT cantidad FROM productos WHERE id = :id"), {"id": product_id})
        prod = result.mappings().first()
        if not prod:
            raise ValueError(f"Producto {product_id} no encontrado")
        
        nuevo_stock = prod["cantidad"] + cantidad_delta
        if nuevo_stock < 0:
            raise ValueError(f"Stock insuficiente para {prod['nombre']}")
        
        conn.execute(
            text("UPDATE productos SET cantidad = :cantidad WHERE id = :id"),
            {"cantidad": nuevo_stock, "id": product_id}
        )
        
        # Opcional: registrar log
        if usuario:
            from .logs import registrar_log
            registrar_log(usuario, "ajustar_stock", {"producto_id": product_id, "delta": cantidad_delta})
        
        return {**prod, "cantidad": nuevo_stock}
    
def update_product(id_producto, nombre, cantidad, precio):
    query = text("""
        UPDATE productos
        SET nombre = :nombre, cantidad = :cantidad, precio = :precio
        WHERE id = :id
    """)
    with engine.begin() as conn:
        conn.execute(query, {"id": id_producto, "nombre": nombre, "cantidad": cantidad, "precio": precio})


def eliminar_producto(id_producto: int, usuario: str = None):
    """
    Elimina un producto de la base de datos por su ID y registra la acción en logs.
    """
    query = text("""
        DELETE FROM productos
        WHERE id = :id_producto
        RETURNING id, nombre
    """)

    with engine.connect() as conn:
        with conn.begin():  # Maneja la transacción
            result = conn.execute(query, {"id_producto": id_producto})
            eliminado = result.mappings().fetchone()  # <-- Mapeo a dict

            if eliminado:
                # Registrar log si se proporciona usuario
                if usuario:
                    registrar_log(usuario, "eliminar_producto", f"Eliminó producto {eliminado['nombre']} (ID {eliminado['id']})")
                return dict(eliminado)  # Retornar como diccionario
            return None
        
        
def increment_stock(producto_id, cantidad):
    """
    Aumenta la cantidad de stock de un producto.
    """
    producto = get_product(producto_id)
    if producto:
        nuevo_stock = float(producto.get("cantidad", 0)) + float(cantidad)
        update_product(producto_id, nombre=producto["nombre"], cantidad=nuevo_stock, precio=producto["precio"])
    else:
        raise ValueError(f"Producto con ID {producto_id} no encontrado")


# ---------------------------
# EXPORTAR INVENTARIO A EXCEL
# ---------------------------
def exportar_inventario_excel() -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from io import BytesIO
    from .categorias import list_categories

    cat_map = {c["id"]: c["nombre"] for c in (list_categories() or [])}
    productos = list_products()

    wb = Workbook()
    ws = wb.active
    ws.title = "Inventario"

    headers = ["Nombre", "Cantidad"]
    ws.append(headers)
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2E75B6")
    for cell in ws[1]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    for p in productos:
        cant = float(p.get("cantidad") or 0)
        ws.append([
            p.get("nombre"),
            cant,
        ])

    for col, ancho in zip("AB", [55, 12]):
        ws.column_dimensions[col].width = ancho
    for row in ws.iter_rows(min_row=2):
        row[1].number_format = '#,##0'

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()