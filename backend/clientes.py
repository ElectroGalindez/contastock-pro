from typing import Dict, Any, Optional
from sqlalchemy import text
from .db import engine
from .logs import registrar_log

def get_client(cliente_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene un cliente por ID"""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, nombre, telefono, ci, chapa, direccion, deuda_total FROM clientes WHERE id = :id"), {"id": cliente_id})
        row = result.first()
        return dict(row._mapping) if row else None



def add_client(nombre, telefono, ci, direccion, chapa, usuario=None):
    """
    Agrega un nuevo cliente a la base de datos.
    Si se pasa 'usuario', se registra quién realizó la acción en la auditoría.
    """
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO clientes (nombre, telefono, ci, direccion, chapa)
                    VALUES (:nombre, :telefono, :ci, :direccion, :chapa)
                """),
                {
                    "nombre": nombre.strip(),
                    "telefono": telefono.strip() if telefono else None,
                    "ci": ci.strip() if ci else None,
                    "direccion": direccion.strip() if direccion else None,
                    "chapa": chapa.strip() if chapa else None
                }
            )

                # Validar tipo de usuario antes de registrar el log
        if usuario and isinstance(usuario, (str, int)):
            registrar_log(
                usuario=str(usuario),
                accion="crear_cliente",
                detalles={
                    "nombre": nombre,
                    "telefono": telefono,
                    "ci": ci,
                    "direccion": direccion,
                    "chapa": chapa
                }
            )

        # ✅ Obtener el cliente recién insertado
        with engine.connect() as conn:
            nuevo = conn.execute(
                text("SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
            ).first()
            return dict(nuevo._mapping) if nuevo else None

    except Exception as e:
        print(f"❌ Error al crear cliente: {e}")
        raise ValueError("Error al registrar el cliente. Verifica los datos e inténtalo nuevamente.")

def update_client(cliente_id: str, nombre=None, telefono=None, ci=None, chapa=None, direccion=None, usuario=None):
    """
    Actualiza los datos de un cliente existente en la base de datos.
    Parámetros opcionales: nombre, telefono, ci, chapa, direccion
    """
    with engine.begin() as conn:
        cliente = get_client(cliente_id)
        if not cliente:
            raise ValueError(f"No existe el cliente con ID {cliente_id}")

        # Preparar los valores nuevos, manteniendo los anteriores si no se pasan
        nombre = nombre or cliente.get("nombre")
        telefono = telefono or cliente.get("telefono", "")
        ci = ci or cliente.get("ci", "")
        chapa = chapa or cliente.get("chapa", "")
        direccion = direccion or cliente.get("direccion", "")

        query = text("""
            UPDATE clientes
            SET nombre = :nombre,
                telefono = :telefono,
                ci = :ci,
                chapa = :chapa,
                direccion = :direccion
            WHERE id = :id
        """)
        conn.execute(query, {
            "id": cliente_id,
            "nombre": nombre,
            "telefono": telefono,
            "ci": ci,
            "chapa": chapa,
            "direccion": direccion
        })

    registrar_log(usuario or "sistema", "update_client", {"id": cliente_id})
    return get_client(cliente_id)

def delete_client(cliente_id: str, usuario: str = "sistema") -> bool:
    """Elimina un cliente"""
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM clientes WHERE id = :id"), {"id": cliente_id})
        registrar_log(usuario, "delete_client", {"id": cliente_id})
        return True
    except Exception as e:
        registrar_log(usuario, "error_delete_client", {"id": cliente_id, "error": str(e)})
        raise

def update_debt(cliente_id: str, monto: float, usuario: str = "sistema") -> Dict[str, Any]:
    """Actualiza la deuda del cliente de manera segura"""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE clientes
                SET deuda_total = CASE WHEN deuda_total + :monto < 0 THEN 0
                                       ELSE deuda_total + :monto END
                WHERE id = :id
            """), {"id": cliente_id, "monto": monto})
        registrar_log(usuario, "update_debt", {"id": cliente_id, "monto": monto})
        return get_client(cliente_id)
    except Exception as e:
        registrar_log(usuario, "error_update_debt", {"id": cliente_id, "monto": monto, "error": str(e)})
        raise


def recalcular_deuda_cliente(cliente_id: str, usuario: str = "sistema") -> float:
    """Recalcula deuda_total del cliente a partir de lo que REALMENTE debe:
    suma de los montos restantes (cantidad * precio) de sus deudas pendientes.
    Se usa al pagar deudas para mantener el saldo sincronizado."""
    try:
        with engine.begin() as conn:
            calculada = conn.execute(text("""
                SELECT COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0)
                FROM deudas_detalle dd
                JOIN deudas d ON d.id = dd.deuda_id
                WHERE d.cliente_id = :id AND d.estado = 'pendiente'
                                  AND dd.estado = 'pendiente'
            """), {"id": cliente_id}).scalar() or 0

            conn.execute(text("""
                UPDATE clientes
                SET deuda_total = :monto
                WHERE id = :id
            """), {"id": cliente_id, "monto": float(calculada)})
        if usuario:
            registrar_log(usuario, "recalcular_deuda", {"id": cliente_id, "monto": float(calculada)})
        return float(calculada)
    except Exception as e:
        if usuario:
            registrar_log(usuario, "error_recalcular_deuda", {"id": cliente_id, "error": str(e)})
        raise

def list_clients(limit=None, offset=None) -> list[Dict[str, Any]]:
    """Lista todos los clientes con campos esenciales"""
    sql = "SELECT id, nombre, telefono, ci, chapa, direccion, deuda_total FROM clientes ORDER BY nombre"
    params = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    if offset is not None:
        sql += " OFFSET :offset"
        params["offset"] = offset
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]

def count_clients() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar()

def edit_client(cliente_id: str, nombre: Optional[str] = None, telefono: Optional[str] = None,
                ci: Optional[str] = None, chapa: Optional[str] = None, direccion: Optional[str] = None,
                usuario: str = "sistema") -> Dict[str, Any]:
    """Edita un cliente existente"""
    cambios = {}
    if nombre is not None:
        cambios["nombre"] = nombre
    if telefono is not None:
        cambios["telefono"] = telefono
    if ci is not None:
        cambios["ci"] = ci
    if chapa is not None:
        cambios["chapa"] = chapa
    if direccion is not None:
        cambios["direccion"] = direccion
    return update_client(cliente_id, cambios, usuario)