"""Configuración central de la aplicación.

Permite que la misma base de código funcione con:
- PostgreSQL (producción actual / Neon): se usa si existe LOCAL_DATABASE_URL o NEON_DATABASE_URL.
- SQLite portátil (instalación de escritorio): base de datos en un archivo local
  que viaja con la app, ideal para instalar en cualquier máquina sin servidor.

También se encarga del directorio de datos y de la secret key persistente
(necesaria para no perder las sesiones al reiniciar la app).
"""
import os
import secrets
from pathlib import Path

from .app_meta import APP_SHORT_NAME


def sys_platform_darwin():
    import platform
    return platform.system() == "Darwin"


def sys_platform_windows():
    import platform
    return platform.system() == "Windows"


# ------------------------------------------------------------------
# Directorio de datos (BD, config, secret key, exportaciones)
# ------------------------------------------------------------------
def _default_data_dir() -> Path:
    override = os.getenv(f"{APP_SHORT_NAME.upper()}_DATA_DIR") or os.getenv("CONTASTOCK_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()

    # Modo empaquetado (PyInstaller) o máquina del usuario
    if sys_platform_darwin():
        base = Path.home() / "Library" / "Application Support"
    elif sys_platform_windows():
        base = Path(os.environ.get("APPDATA", str(Path.home())))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))

    return base / APP_SHORT_NAME


DATA_DIR = _default_data_dir()


def get_database_path() -> Path:
    """Ruta del archivo SQLite portátil dentro del directorio de datos."""
    return DATA_DIR / "contastock.db"


def sqlite_database_url() -> str:
    """URL SQLAlchemy para la BD SQLite portátil."""
    path = get_database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path.as_posix()}"


def database_url() -> str:
    """URL de la BD a usar en runtime: override local (LOCAL_DATABASE_URL) o
    SQLite portátil por defecto. Neon ya no se selecciona automáticamente;
    solo el script de migración (`scripts/migrar_neon_a_local.py`) lo lee."""
    return os.getenv("LOCAL_DATABASE_URL") or sqlite_database_url()


def neon_database_url() -> str | None:
    """URL de Neon (solo para traer los datos a local). Se puede borrar cuando
    la migración esté terminada."""
    return os.getenv("NEON_DATABASE_URL")


def get_secret_key() -> str:
    """Secret key persistente de Flask. Se genera una sola vez por instalación."""
    secret_file = DATA_DIR / "secret.key"
    if secret_file.exists():
        return secret_file.read_text().strip()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32)
    secret_file.write_text(key)
    return key


def resource_path(rel_path: str) -> Path:
    """Devuelve la ruta absoluta de un recurso empaquetado con la app.

    En modo normal devuelve 'rel_path'; en modo PyInstaller (frozen) devuelve
    la ruta dentro de sys._MEIPASS."""
    import sys
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
        return base / rel_path
    return Path(os.path.abspath(__file__)).parent.parent / rel_path