"""Secretos compartidos por GitHub, cifrados (.env.cifrado).

La URL de Neon viaja en el repo cifrada, así al actualizar la PC del almacén
con `git pull` también llega la conexión. Se descifra al arrancar, en memoria:
nunca queda escrita en claro en el disco.

La clave NO va en el repo (sin ella el archivo no sirve para nada). Vive en
`.env.clave`, junto al proyecto, y se copia una sola vez a cada PC, o en la
variable de entorno CONTASTOCK_CLAVE.

Solo se comparten las variables de COMPARTIDAS. Lo que es de cada PC (por
ejemplo NEON_SYNC=1, que solo lleva el almacén) sigue en el `.env` local.

Uso:
    python scripts/secretos.py cifrar          # .env -> .env.cifrado (crea la clave si falta)
    python scripts/secretos.py verificar       # ¿esta PC puede leerlo?
    python scripts/secretos.py guardar-clave CLAVE
"""
import logging
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / ".env.cifrado"
ARCHIVO_CLAVE = RAIZ / ".env.clave"
COMPARTIDAS = ("NEON_DATABASE_URL",)

log = logging.getLogger("contastock.secretos")


def _clave() -> bytes | None:
    valor = os.getenv("CONTASTOCK_CLAVE", "").strip()
    if not valor and ARCHIVO_CLAVE.exists():
        valor = ARCHIVO_CLAVE.read_text().strip()
    return valor.encode() if valor else None


def _parsear(texto: str) -> dict:
    valores = {}
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        valores[k.strip()] = v.strip().strip('"').strip("'")
    return valores


def leer() -> dict:
    """Variables del archivo cifrado, o {} si no hay archivo o clave."""
    clave = _clave()
    if not ARCHIVO.exists() or not clave:
        return {}
    from cryptography.fernet import Fernet, InvalidToken
    try:
        texto = Fernet(clave).decrypt(ARCHIVO.read_bytes().strip()).decode()
    except (InvalidToken, ValueError):
        log.warning(".env.cifrado no se pudo leer: la clave de .env.clave no corresponde")
        return {}
    return _parsear(texto)


def cargar() -> list[str]:
    """Pone en el entorno las variables cifradas que no estén ya definidas.

    Se llama ANTES de leer el .env local, así la URL del repo (la vigente)
    gana sobre una vieja que haya quedado en el .env de la PC. Lo que ya está
    en el entorno del sistema (o lo que fijan los tests) se respeta.
    """
    cargadas = []
    for k, v in leer().items():
        if k in COMPARTIDAS and k not in os.environ:
            os.environ[k] = v
            cargadas.append(k)
    return cargadas


def cifrar(valores: dict) -> bool:
    """Escribe .env.cifrado. Devuelve True si tuvo que crear una clave nueva."""
    from cryptography.fernet import Fernet
    nueva = False
    clave = _clave()
    if not clave:
        clave = Fernet.generate_key()
        ARCHIVO_CLAVE.write_text(clave.decode() + "\n")
        nueva = True
    texto = "".join(f"{k}={v}\n" for k, v in valores.items() if k in COMPARTIDAS and v)
    ARCHIVO.write_bytes(Fernet(clave).encrypt(texto.encode()) + b"\n")
    return nueva


def _main(args) -> int:
    orden = args[0] if args else ""
    if orden == "cifrar":
        from dotenv import dotenv_values
        valores = {k: v for k, v in dotenv_values(RAIZ / ".env").items()
                   if k in COMPARTIDAS and v}
        if not valores:
            print("El .env no tiene NEON_DATABASE_URL: no hay nada que cifrar.")
            return 1
        nueva = cifrar(valores)
        print(f"Listo: {ARCHIVO.name} ({', '.join(valores)}). Este SÍ se sube a GitHub.")
        if nueva:
            print(f"Se creó la clave en {ARCHIVO_CLAVE.name}. NO se sube a GitHub:")
            print("copiá ese archivo a la carpeta del proyecto en el almacén.")
        return 0
    if orden == "verificar":
        if not ARCHIVO.exists():
            print("No hay .env.cifrado en esta carpeta.")
            return 1
        if not _clave():
            print("Falta la clave (.env.clave). Copiala desde la Mac.")
            return 1
        valores = leer()
        if not valores:
            print("La clave no corresponde a este .env.cifrado.")
            return 1
        print("OK: esta PC puede leer", ", ".join(valores))
        return 0
    if orden == "guardar-clave" and len(args) > 1:
        ARCHIVO_CLAVE.write_text(args[1].strip() + "\n")
        return _main(["verificar"])
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
