"""
Guarda una copia de respaldo de la base local antes de actualizar la app.

La copia se hace con la API de backup de SQLite, así sale consistente aunque
la app esté abierta. Queda en la carpeta de datos, dentro de `respaldos/`, y
se conservan las últimas 20.

Uso:
    python scripts/respaldar_base.py
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from backend.config import get_database_path  # noqa: E402

CONSERVAR = 20


def main() -> int:
    origen = get_database_path()
    if not origen.exists():
        print(f"No hay base local en {origen}: nada que respaldar.")
        return 0

    carpeta = origen.parent / "respaldos"
    carpeta.mkdir(exist_ok=True)
    destino = carpeta / f"contastock_{datetime.now():%Y%m%d_%H%M%S}.db"

    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(destino))
    try:
        src.backup(dst)
        ventas = dst.execute("SELECT COUNT(*) FROM ventas").fetchone()[0]
    finally:
        dst.close()
        src.close()

    viejos = sorted(carpeta.glob("contastock_*.db"))[:-CONSERVAR]
    for archivo in viejos:
        archivo.unlink()

    print(f"Respaldo guardado: {destino} ({ventas} ventas)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
