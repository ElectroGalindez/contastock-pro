import os
from datetime import datetime
from sqlalchemy import (
    create_engine, MetaData, text, Table, Column, Integer, String, Numeric,
    Boolean, DateTime, Text, ForeignKey, JSON,
)
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager
from sqlalchemy.pool import SingletonThreadPool

from .config import database_url

# ---------------------------
# Cargar .env
# ---------------------------
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path)

DATABASE_URL = database_url()

# ---------------------------
# Dialecto activo
# ---------------------------
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# ---------------------------
# Motor y sesión
# ---------------------------
if IS_SQLITE:
    _connect_args = {"check_same_thread": False, "timeout": 30}
    engine = create_engine(
        DATABASE_URL, echo=False, future=True,
        connect_args=_connect_args,
        poolclass=SingletonThreadPool,
    )
else:
    engine = create_engine(DATABASE_URL, echo=False, future=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Objeto MetaData global con el esquema de todas las tablas.
# Sirve para CREAR el esquema automáticamente (SQLite portátil) y como
# referencia de tipos. Las tablas existentes no se tocan.
metadata = MetaData()

usuarios_table = Table(
    "usuarios", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String, nullable=False),
    Column("password", String, nullable=False),
    Column("nombre", String),
    Column("activo", Boolean, server_default="1"),
    Column("intentos_fallidos", Integer, default=0),
    Column("bloqueado_hasta", DateTime),
    Column("rol", String, default="usuario"),
    Column("cambiar_password", Boolean, server_default="0"),
    Column("requiere_cambio_password", Boolean, server_default="1"),
    Column("created_at", DateTime),
)

categorias_table = Table(
    "categorias", metadata,
    Column("id", Integer, primary_key=True),
    Column("nombre", String, nullable=False),
)

productos_table = Table(
    "productos", metadata,
    Column("id", Integer, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("precio", Numeric, nullable=False),
    Column("categoria_id", Integer, ForeignKey("categorias.id")),
    Column("cantidad", Integer, nullable=False, default=0),
)

clientes_table = Table(
    "clientes", metadata,
    Column("id", Integer, primary_key=True),
    Column("nombre", Text, nullable=False),
    Column("direccion", Text),
    Column("telefono", Text),
    Column("ci", String),
    Column("chapa", String),
    Column("deuda_total", Numeric, server_default="0"),
    Column("creado_por", Text),
)

ventas_table = Table(
    "ventas", metadata,
    Column("id", Integer, primary_key=True),
    Column("cliente_id", Integer, ForeignKey("clientes.id")),
    Column("fecha", DateTime),
    Column("subtotal", Numeric),
    Column("pagado", Numeric),
    Column("saldo", Numeric),
    Column("productos_vendidos", JSON, nullable=False, default=list),
    Column("total", Numeric, default=0),
    Column("tipo_pago", Text),
    Column("usuario", Text),
    Column("observaciones", Text),
    Column("vendedor", String),
    Column("telefono_vendedor", String),
    Column("chofer", String),
    Column("chapa", String),
)

deudas_table = Table(
    "deudas", metadata,
    Column("id", Integer, primary_key=True),
    Column("venta_id", Integer, ForeignKey("ventas.id")),
    Column("monto", Numeric),
    Column("estado", Text, default="pendiente"),
    Column("cliente_id", Integer, ForeignKey("clientes.id")),
    Column("productos", JSON, default=list),
    Column("monto_total", Numeric, default=0),
    Column("fecha", DateTime),
    Column("descripcion", Text, default=""),
)

deudas_detalle_table = Table(
    "deudas_detalle", metadata,
    Column("id", Integer, primary_key=True),
    Column("deuda_id", Integer, ForeignKey("deudas.id", ondelete="CASCADE"), nullable=False),
    Column("producto_id", Integer),
    Column("cantidad", Numeric, nullable=False, default=0),
    Column("precio_unitario", Numeric, nullable=False, default=0),
    Column("monto", Numeric),
    Column("estado", String, default="pendiente"),
)

pagos_deudas_table = Table(
    "pagos_deudas", metadata,
    Column("id", Integer, primary_key=True),
    Column("deuda_id", Integer, ForeignKey("deudas.id", ondelete="CASCADE")),
    Column("detalle_id", Integer),
    Column("producto_id", Integer),
    Column("producto_nombre", Text, default=""),
    Column("cantidad", Numeric, default=0),
    Column("precio_unitario", Numeric, default=0),
    Column("monto_pagado", Numeric, default=0),
    Column("metodo_pago", Text, default="Efectivo"),
    Column("observaciones", Text, default=""),
    Column("fecha", DateTime),
    Column("usuario", Text, default="sistema"),
    Column("cliente_id", Integer, ForeignKey("clientes.id")),
)

ventas_detalle_table = Table(
    "ventas_detalle", metadata,
    Column("id", Integer, primary_key=True),
    Column("venta_id", Integer, ForeignKey("ventas.id", ondelete="CASCADE")),
    Column("producto_id", Integer, ForeignKey("productos.id")),
    Column("cantidad", Numeric, nullable=False, default=0),
    Column("precio_unitario", Numeric, nullable=False, default=0),
    Column("monto", Numeric),
)

logs_table = Table(
    "logs", metadata,
    Column("id", Integer, primary_key=True),
    Column("usuario", String, nullable=False),
    Column("accion", Text, nullable=False),
    Column("detalles", Text),
    Column("fecha", DateTime, nullable=False),
)

auditoria_table = Table(
    "auditoria", metadata,
    Column("id", Integer, primary_key=True),
    Column("accion", String, nullable=False),
    Column("producto_id", Integer),
    Column("usuario", String, nullable=False),
    Column("fecha", DateTime),
)


def ensure_schema():
    """Crea las tablas que falten (solo si no existen).
    No altera tablas existentes, por lo que es seguro en producción."""
    if IS_SQLITE:
        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    metadata.create_all(engine)


ensure_schema()

# ---------------------------
# Helpers de SQL portable (PostgreSQL y SQLite)
# ---------------------------
def extract_month(col: str) -> str:
    """EXTRACT(MONTH FROM col) portable."""
    if IS_SQLITE:
        return f"CAST(strftime('%m', {col}) AS INTEGER)"
    return f"EXTRACT(MONTH FROM {col})"


def extract_year(col: str) -> str:
    """EXTRACT(YEAR FROM col) portable."""
    if IS_SQLITE:
        return f"CAST(strftime('%Y', {col}) AS INTEGER)"
    return f"EXTRACT(YEAR FROM {col})"


def month_key(col: str) -> str:
    """Clave 'YYYY-MM' portable para agrupar ventas por mes."""
    if IS_SQLITE:
        return f"strftime('%Y-%m', {col})"
    return f"TO_CHAR({col}, 'YYYY-MM')"


def dm_key(col: str) -> str:
    """Clave 'DD/MM' portable para agrupar ventas por día."""
    if IS_SQLITE:
        return f"strftime('%d/%m', {col})"
    return f"TO_CHAR({col}, 'DD/MM')"


def ilike() -> str:
    """Operador de búsqueda de texto portable (ILIKE en PostgreSQL, LIKE en SQLite)."""
    return "ILIKE" if not IS_SQLITE else "LIKE"


def ensure_datetime(value):
    """Normaliza un valor de fecha devuelto por la BD.

    PostgreSQL devuelve datetime nativos, pero SQLite devuelve strings
    ('YYYY-MM-DD HH:MM:SS'). Este helper garantiza datetime en ambos casos."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return value


# ---------------------------
# Context manager para conexión
# ---------------------------
@contextmanager
def get_connection():
    """
    Devuelve una sesión de SQLAlchemy (context manager).
    Uso:
       with get_connection() as session:
           session.execute(...)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ---------------------------
# Función de prueba
# ---------------------------
def test_connection():
    with engine.connect() as conn:
        if IS_SQLITE:
            result = conn.execute(text("SELECT 1"))
        else:
            result = conn.execute(text("SELECT NOW()"))
        print("Conexión exitosa ✅", result.scalar())

if __name__ == "__main__":
    test_connection()