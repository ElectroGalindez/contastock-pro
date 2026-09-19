"""
Configuración de pruebas. Aísla los tests en la BD 'electrogalindez_test'
para no tocar la base de datos real del negocio.
"""
import os

# Punto la BD a la de pruebas ANTES de importar el backend
os.environ["LOCAL_DATABASE_URL"] = (
    "postgresql://postgres:electrogalindez2026@localhost:5432/electrogalindez_test"
)

import pytest
from sqlalchemy import text

from backend.db import engine


@pytest.fixture(scope="session", autouse=True)
def schema():
    """Recrea el esquema vacío una vez por sesión de tests."""
    from pathlib import Path
    import re

    src = Path("setup_local_db.py").read_text()
    ns = {}
    exec(re.search(r"SCHEMA_SQL = \"\"\".*?\"\"\"", src, re.S).group(0), ns)
    with engine.begin() as conn:
        conn.execute(text(ns["SCHEMA_SQL"]))
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    """Limpia las tablas de negocio entre cada test."""
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE pagos_deudas, deudas_detalle, deudas, ventas_detalle, "
                          "ventas, productos, clientes, categorias, usuarios, logs RESTART IDENTITY CASCADE"))
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