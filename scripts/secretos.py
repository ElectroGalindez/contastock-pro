"""
Atajo para backend/secretos.py (cifrar la URL de Neon para subirla a GitHub).

    python scripts/secretos.py cifrar
    python scripts/secretos.py verificar
    python scripts/secretos.py guardar-clave CLAVE

Carga el módulo por su ruta para no inicializar toda la app (ni la base).
"""
import importlib.util
import sys
from pathlib import Path

ruta = Path(__file__).resolve().parent.parent / "backend" / "secretos.py"
spec = importlib.util.spec_from_file_location("secretos", ruta)
secretos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(secretos)

raise SystemExit(secretos._main(sys.argv[1:]))
