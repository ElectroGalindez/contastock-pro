# backend/logs.py
from datetime import datetime
from typing import List, Dict, Any
from sqlalchemy import text
import json
from .db import engine  # Función que devuelve conexión SQLAlchemy

# ---------------------------
# Registrar un log
# ---------------------------
def registrar_log(usuario: str, accion: str, detalles):
    """
    Registra una acción en la tabla de logs.
    Convierte dict o list a JSON string antes de guardar.
    """

    # Convertir detalles a JSON si es dict o list
    if isinstance(detalles, (dict, list)):
        detalles = json.dumps(detalles, default=str)

    # Asegurar que usuario sea string
    if isinstance(usuario, dict):
        usuario = usuario.get("username", "sistema")

    fecha = datetime.now()

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO logs (usuario, accion, detalles, fecha)
                VALUES (:usuario, :accion, :detalles, :fecha)
            """),
            {
                "usuario": usuario,
                "accion": accion,
                "detalles": detalles,
                "fecha": fecha
            }
        )

# ---------------------------
# Listar todos los logs
# ---------------------------
def listar_logs(limit=None, offset=None) -> List[Dict[str, Any]]:
    sql = "SELECT * FROM logs ORDER BY fecha DESC"
    params = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    if offset is not None:
        sql += " OFFSET :offset"
        params["offset"] = offset
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(row) for row in result.mappings().all()]


def contar_logs() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM logs")).scalar()


def obtener_logs_usuario(username: str):
    """Devuelve los registros del historial de acciones de un usuario."""
    query = text("""
        SELECT usuario, accion, fecha, detalles
        FROM logs
        WHERE usuario = :usuario
        ORDER BY fecha DESC
        LIMIT 100
    """)
    with engine.connect() as conn:
        result = conn.execute(query, {"usuario": username})
        return [dict(row) for row in result.mappings().all()]