"""
Trae los datos de Neon (PostgreSQL) a la base SQLite portátil local.

La app usa SQLite local por defecto (ver `backend/config.py`). Este script
lee `NEON_DATABASE_URL` del `.env`, copia todas las tablas a la base SQLite
local y garantiza el usuario `admin` / `admin1234` para poder entrar.

Cuando la migración esté verificada se puede borrar la conexión a Neon del
`.env` sin afectar a la app.

Ojo: REEMPLAZA la base local. Si la base local ya tiene ventas (por ejemplo,
la PC del almacén, donde la base buena es la local) no hace nada salvo que se
pase --forzar, y aun así guarda antes una copia de respaldo.

Uso:
    python scripts/migrar_neon_a_local.py            # solo si la base local está vacía
    python scripts/migrar_neon_a_local.py --forzar   # pisa la local (con respaldo)
"""
import sys
import argparse
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv
load_dotenv(RAIZ / ".env")

from sqlalchemy import create_engine, MetaData, Table, select, event, text
from sqlalchemy.exc import NoSuchTableError

from backend import config, db
from backend.usuarios import asegurar_admin

# Orden de copia respetando dependencias (padres primero).
ORDER = [
    "usuarios", "categorias", "clientes", "productos",
    "ventas", "ventas_detalle", "deudas", "deudas_detalle",
    "pagos_deudas", "logs", "auditoria",
]


def engine_sin_fk(url: str):
    """Engine de destino con claves foráneas desactivadas (Neon tiene huérfanos)."""
    eng = create_engine(url, future=True)
    if eng.dialect.name == "sqlite":
        @event.listens_for(eng, "connect")
        def _fk_off(dbapi_connection, connection_record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=OFF")
            cur.close()
    return eng


def _ventas_locales() -> int:
    ruta = config.get_database_path()
    if not ruta.exists():
        return 0
    import sqlite3
    con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True)
    try:
        return con.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def _respaldar() -> Path:
    """Copia consistente de la base local antes de pisarla."""
    import sqlite3
    from datetime import datetime
    origen = config.get_database_path()
    destino = origen.with_name(f"respaldo_{datetime.now():%Y%m%d_%H%M%S}.db")
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True)
    dst = sqlite3.connect(str(destino))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return destino


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrar datos de Neon a SQLite local")
    parser.add_argument("--neon", default=None,
                        help="URL de Neon (por defecto NEON_DATABASE_URL del .env)")
    parser.add_argument("--forzar", action="store_true",
                        help="Reemplazar la base local aunque ya tenga ventas")
    args = parser.parse_args()

    ventas_locales = _ventas_locales()
    if ventas_locales and not args.forzar:
        print(f"La base local ya tiene {ventas_locales} ventas: NO se reemplaza.")
        print("En la PC del almacén la base buena es la local; no hace falta bajar Neon.")
        print("Si de verdad querés pisarla con lo de Neon, usá --forzar.")
        return 0
    if ventas_locales:
        print(f"Respaldo de la base local: {_respaldar()}")

    src_url = args.neon or config.neon_database_url()
    if not src_url:
        print("ERROR: no hay NEON_DATABASE_URL configurada en .env")
        return 1

    dest_url = config.sqlite_database_url()
    print(f"Origen : ...{src_url.split('@')[-1]}")
    print(f"Destino: {dest_url}\n")

    src = create_engine(src_url, future=True)
    dest = engine_sin_fk(dest_url)
    db.metadata.create_all(dest)

    src_meta = MetaData()
    resumen = []

    with dest.begin() as conn:
        # Vaciar el destino (hijos primero) para que la copia sea idempotente.
        for name in reversed(ORDER):
            if name in db.metadata.tables:
                conn.execute(text(f"DELETE FROM {name}"))
        if dest.dialect.name == "sqlite":
            tiene_seq = conn.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='sqlite_sequence'"
            )).first()
            if tiene_seq:
                conn.execute(text("DELETE FROM sqlite_sequence"))

        for name in ORDER:
            if name not in db.metadata.tables:
                continue
            try:
                src_tbl = Table(name, src_meta, autoload_with=src)
            except NoSuchTableError:
                print(f"  {name:18} (no existe en Neon, se omite)")
                continue

            dest_tbl = db.metadata.tables[name]
            cols = [c.name for c in dest_tbl.columns if c.name in src_tbl.c]
            if not cols:
                print(f"  {name:18} (sin columnas en común, se omite)")
                continue

            with src.connect() as sconn:
                rows = [dict(r) for r in sconn.execute(
                    select(*[src_tbl.c[c] for c in cols])
                ).mappings().all()]

            if rows:
                conn.execute(dest_tbl.insert(), rows)
            resumen.append((name, len(rows)))
            print(f"  {name:18} {len(rows)} filas")

    accion = asegurar_admin("admin", "admin1234", reset=True)
    total = sum(n for _, n in resumen)

    print(f"\nUsuario admin/admin1234: {accion}")
    print(f"Total migrado: {total} filas")
    print("\nListo. La app ya usa los datos locales (SQLite).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
