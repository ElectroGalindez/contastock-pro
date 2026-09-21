"""
Configuración de pruebas. Aísla los tests en una base SQLite portátil temporal
(verificando además que toda la app funciona sobre SQLite, no solo PostgreSQL).
"""
import os
import tempfile

_TMP = tempfile.mkdtemp(prefix="contastock_test_")

# Forzar SQLite portátil ANTES de importar el backend.
os.environ["CONTASTOCK_DATA_DIR"] = _TMP
os.environ["LOCAL_DATABASE_URL"] = ""
os.environ["NEON_DATABASE_URL"] = ""

import pytest
from sqlalchemy import text

from backend import db


@pytest.fixture(scope="session", autouse=True)
def schema():
    """Recrea el esquema vacío una vez por sesión de pruebas."""
    with db.engine.begin() as conn:
        db.metadata.drop_all(db.engine)
    db.metadata.create_all(db.engine)
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    """Limpia las tablas de negocio entre cada test (portable SQLite/PostgreSQL)."""
    TABLES = ["pagos_deudas", "deudas_detalle", "deudas", "ventas_detalle",
              "ventas", "productos", "clientes", "categorias", "usuarios", "logs"]
    with db.engine.begin() as conn:
        if db.IS_SQLITE:
            conn.execute(text("PRAGMA foreign_keys=OFF"))
            for t in TABLES:
                conn.execute(text(f"DELETE FROM {t}"))
            seq = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'")).first()
            if seq:
                conn.execute(text("DELETE FROM sqlite_sequence"))
            conn.execute(text("PRAGMA foreign_keys=ON"))
        else:
            conn.execute(text("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def cliente():
    from backend.clientes import add_client
    return add_client(
        nombre="Cliente Prueba", telefono="5551234",
        ci=None, direccion=None, chapa=None
    )


@pytest.fixture
def producto():
    from backend.categorias import agregar_categoria, list_categories
    from backend.productos import guardar_producto
    agregar_categoria("Test")
    cat_id = list_categories()[-1]["id"]
    return guardar_producto("Producto Test", 100.0, 10, cat_id, "test")