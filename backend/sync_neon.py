"""Sube la base SQLite local a Neon cada cierto tiempo, para verla desde otro lugar.

La base local sigue siendo la única fuente de verdad: este módulo solo la LEE.
Neon es un espejo de solo lectura (por ejemplo, para que el jefe vea el
almacén desde la casa).

Cómo se evita dañar datos cuando se corta la conexión:

- Base local: no se escribe nunca. Se toma una foto consistente con la API de
  backup de SQLite y se trabaja sobre esa copia, así la app puede seguir
  vendiendo mientras se sube.
- Neon: cada subida va en UNA sola transacción. Si se corta internet a la
  mitad, Postgres hace rollback y Neon se queda con la copia anterior
  completa, nunca con una a medias.

Cuándo sube: en cuanto la app guarda algo (venta, pago, cliente...), la
ruta avisa con avisar_cambio() y a los pocos segundos se sube, agrupando
los cambios que lleguen juntos. Además, cada NEON_SYNC_MINUTOS se revisa por
si algo cambió por otro lado (scripts), y si falló por falta de internet se
reintenta a los 2 minutos.

Para no gastar de más del plan de Neon:
- Si nada cambió desde la última subida, no se conecta (no lo despierta).
- Solo se suben las filas nuevas, cambiadas o borradas, no la base entera.
- Las conexiones se cierran al terminar (una conexión abierta puede impedir
  que Neon se duerma).
- Si la base local parece vacía o mucho más chica que Neon (por ejemplo, la
  app abierta en otra PC que no es la del almacén), no se sube: pisaría los
  datos buenos.

Configuración (.env) — SOLO en la PC del almacén:
    NEON_SYNC=1                          # sin esto no sube nada, aunque esté la URL
    NEON_DATABASE_URL=postgresql://...
    NEON_SYNC_MINUTOS=5                  # revisión periódica además de los avisos (0 = no)
    NEON_SYNC_HORAS=13:00,19:30          # además, a estas horas fijas (opcional)
"""
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import threading
import time
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import (
    Boolean, Date, DateTime, Integer, JSON, Numeric, create_engine, inspect, text,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.pool import NullPool

from . import config, db

log = logging.getLogger("contastock.sync_neon")

# Padres antes que hijos (mismo orden que scripts/migrar_local_a_neon.py).
TABLAS = [
    "usuarios", "categorias", "productos", "clientes",
    "ventas", "ventas_detalle", "deudas", "deudas_detalle",
    "pagos_deudas", "logs", "auditoria",
]
LOTE = 1000
TIMEOUT_CONEXION = 15        # segundos para darse cuenta de que no hay internet
REINTENTO_MIN = 2            # tras un fallo (sin internet), reintentar a los 2 minutos
ESPERA_AGRUPAR = 4           # segundos sin cambios nuevos antes de subir
ESPERA_MAX = 20              # aunque sigan llegando cambios, subir a los 20 s
LATIDO_MIN = 120             # sin cambios, avisar a Neon "sigo conectado" cada 2 h
MINUTOS_DEFECTO = 5

_lock = threading.Lock()
_hilo = None
_cambio = threading.Event()   # la app avisa que guardó algo


# ======================================================
# Estado (último resultado, para mostrarlo en la app)
# ======================================================
def _dir_sync() -> Path:
    carpeta = config.DATA_DIR / "sync"
    carpeta.mkdir(parents=True, exist_ok=True)
    return carpeta


def leer_estado() -> dict:
    try:
        return json.loads((_dir_sync() / "estado.json").read_text())
    except (OSError, ValueError):
        return {}


def _guardar_estado(**cambios) -> dict:
    """Escritura atómica: si la app se cierra a la mitad no queda un JSON roto."""
    estado = leer_estado()
    estado.update(cambios)
    destino = _dir_sync() / "estado.json"
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, indent=2))
    os.replace(tmp, destino)
    return estado


def configurada() -> bool:
    """Solo sube la PC que se marcó a propósito con NEON_SYNC=1 (la del almacén).

    Tener la URL de Neon no alcanza: la PC de desarrollo también la tiene (para
    los scripts de migración) y subir su base de pruebas pisaría los datos
    reales del almacén.
    """
    activa = os.getenv("NEON_SYNC", "").strip().lower() in ("1", "true", "si", "sí")
    return activa and bool(config.neon_database_url()) and db.IS_SQLITE


# ======================================================
# Conversión SQLite -> PostgreSQL
# ======================================================
def _a_bool(v):
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    return str(v).strip().lower() in ("1", "true", "t", "yes", "si", "s")


def _a_decimal(v):
    if v is None or isinstance(v, Decimal):
        return v
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None


def _a_datetime(v):
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    texto = str(v).strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        return None


def _a_json(v):
    if not isinstance(v, str):
        return v
    try:
        return json.loads(v) if v.strip() else None
    except ValueError:
        return None


_CONVERSORES = [
    (Boolean, _a_bool), (Numeric, _a_decimal), (DateTime, _a_datetime),
    (Date, _a_datetime), (JSON, _a_json),
    (Integer, lambda v: None if v in (None, "") else int(v)),
]


def _convertir(valor, tipo):
    for clase, funcion in _CONVERSORES:
        if isinstance(tipo, clase):
            return funcion(valor)
    return valor


# ======================================================
# Foto de la base local
# ======================================================
def tomar_foto(origen: Path, destino: Path) -> None:
    """Copia consistente de la base SQLite aunque la app esté escribiendo."""
    src = sqlite3.connect(f"file:{origen}?mode=ro", uri=True, timeout=30)
    dst = sqlite3.connect(str(destino))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def leer_foto(ruta: Path) -> dict:
    """{tabla: (columnas, filas)} de las tablas que existan en la foto."""
    con = sqlite3.connect(str(ruta))
    try:
        existentes = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        datos = {}
        for tabla in TABLAS:
            if tabla not in existentes:
                continue
            cur = con.execute(f'SELECT * FROM "{tabla}" ORDER BY id')
            columnas = [c[0] for c in cur.description]
            datos[tabla] = (columnas, cur.fetchall())
        return datos
    finally:
        con.close()


def firmas(datos: dict) -> dict:
    """{tabla: {id: hash de la fila}}. Comparándolas con las de la última
    subida se sabe exactamente qué filas cambiaron."""
    salida = {}
    for tabla in TABLAS:
        if tabla not in datos:
            continue
        columnas, filas = datos[tabla]
        i = columnas.index("id")
        cab = repr(columnas).encode()
        salida[tabla] = {
            str(fila[i]): hashlib.blake2b(cab + repr(fila).encode(), digest_size=8).hexdigest()
            for fila in filas
        }
    return salida


def huella(datos: dict) -> str:
    """Hash de todo el contenido (para comparar dos fotos de un vistazo)."""
    return hashlib.sha256(json.dumps(firmas(datos), sort_keys=True).encode()).hexdigest()


def diferencias(previas: dict, nuevas: dict) -> dict:
    """{tabla: (ids a subir, ids a borrar)} solo de las tablas que cambiaron."""
    cambios = {}
    for tabla in TABLAS:
        antes, ahora = previas.get(tabla, {}), nuevas.get(tabla, {})
        subir = [i for i, h in ahora.items() if antes.get(i) != h]
        borrar = [i for i in antes if i not in ahora]
        if subir or borrar:
            cambios[tabla] = (subir, borrar)
    return cambios


def _leer_subido():
    """Firmas de lo que hay en Neon según la última subida buena, o None."""
    try:
        return json.loads((_dir_sync() / "subido.json").read_text())
    except (OSError, ValueError):
        return None


def _guardar_subido(firmas_nuevas: dict) -> None:
    destino = _dir_sync() / "subido.json"
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(firmas_nuevas))
    os.replace(tmp, destino)


# ======================================================
# Subida a Neon
# ======================================================
def _motor_neon(url):
    args = {"connect_timeout": TIMEOUT_CONEXION} if url.startswith("postgresql") else {}
    # NullPool: la conexión se cierra al terminar y no deja a Neon despierto
    return create_engine(url, future=True, poolclass=NullPool, connect_args=args)


def _claves_foraneas(conn):
    return [dict(f) for f in conn.execute(text("""
        SELECT tc.constraint_name, tc.table_name, kcu.column_name,
               ccu.table_name AS tabla_ref, ccu.column_name AS columna_ref,
               rc.update_rule, rc.delete_rule
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.constraint_schema = kcu.table_schema
        JOIN information_schema.constraint_column_usage ccu
          ON ccu.constraint_name = tc.constraint_name
         AND ccu.table_schema = tc.constraint_schema
        JOIN information_schema.referential_constraints rc
          ON rc.constraint_name = tc.constraint_name
         AND rc.constraint_schema = tc.constraint_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
    """)).mappings().all()]


def _preparar_esquema(motor):
    """Crea tablas y columnas que falten. Es idempotente y no toca datos."""
    db.metadata.create_all(motor)
    inspector = inspect(motor)
    for tabla in TABLAS:
        actuales = {c["name"] for c in inspector.get_columns(tabla)}
        for columna in db.metadata.tables[tabla].columns:
            if columna.name in actuales:
                continue
            tipo = columna.type.compile(motor.dialect)
            default = ""
            if columna.server_default is not None:
                arg = columna.server_default.arg
                default = f" DEFAULT {getattr(arg, 'text', arg)}"
            with motor.begin() as conn:
                conn.execute(text(
                    f'ALTER TABLE "{tabla}" ADD COLUMN "{columna.name}" {tipo}{default}'))


def _contar_neon(conn) -> dict:
    return {t: conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar() or 0
            for t in TABLAS}


def _parece_otra_base(local: dict, remoto: dict) -> str | None:
    """Evita que una PC con la base vacía o vieja pise los datos de Neon."""
    total_local = sum(local.values())
    total_remoto = sum(remoto.values())
    if total_remoto and not local.get("ventas") and remoto.get("ventas"):
        return "la base local no tiene ventas y Neon sí"
    if total_remoto and total_local < total_remoto * 0.5:
        return (f"la base local tiene {total_local} filas y Neon {total_remoto}; "
                "parece otra base (¿la app está abierta en otra PC?)")
    return None


def _subir(motor, datos: dict, cambios: dict | None, previas: dict | None) -> dict:
    """Sube a Neon en una sola transacción.

    Con `cambios` sube solo esas filas (lo normal). Sin `cambios` (primera vez,
    o si Neon no coincide con lo que se subió antes) reemplaza todo.
    Devuelve {"modo": "cambios"|"completa", "filas": n}.
    """
    _preparar_esquema(motor)
    inspector = inspect(motor)
    tipos = {t: {c["name"]: c["type"] for c in inspector.get_columns(t)} for t in TABLAS}

    with motor.begin() as conn:
        # Si dos PCs del almacén tuvieran la URL, que no suban a la vez.
        if not conn.execute(text("SELECT pg_try_advisory_xact_lock(724501)")).scalar():
            raise RuntimeError("otra PC está subiendo en este momento")
        # Que nunca quede colgada esperando un bloqueo de quien está mirando.
        conn.execute(text("SET LOCAL lock_timeout = '30s'"))

        remoto = _contar_neon(conn)
        local = {t: len(datos[t][1]) if t in datos else 0 for t in TABLAS}
        motivo = _parece_otra_base(local, remoto)
        if motivo:
            raise RuntimeError(f"no se subió: {motivo}")

        # Si Neon no tiene lo que creemos que tiene (alguien lo tocó, o se
        # perdió subido.json), los cambios sueltos no alcanzan: todo de nuevo.
        if cambios is not None and any(
                remoto[t] != len((previas or {}).get(t, {})) for t in TABLAS):
            cambios = None

        generadas = {}
        for tabla, columna in conn.execute(text("""
            SELECT table_name, column_name FROM information_schema.columns
            WHERE table_schema = 'public' AND is_generated = 'ALWAYS'
        """)):
            generadas.setdefault(tabla, set()).add(columna)

        # Neon es solo un espejo: las claves foráneas no aportan (la base
        # local ya las controla) y harían fallar filas huérfanas viejas que
        # SQLite aceptó. Se quitan una vez y no se vuelven a poner.
        for fk in _claves_foraneas(conn):
            conn.execute(text(f'ALTER TABLE "{fk["table_name"]}" '
                              f'DROP CONSTRAINT IF EXISTS "{fk["constraint_name"]}"'))

        def filas_para(tabla, solo_ids=None):
            columnas, filas = datos[tabla]
            usar = [c for c in columnas
                    if c in tipos[tabla] and c in db.metadata.tables[tabla].c
                    and c not in generadas.get(tabla, ())]
            indices = [columnas.index(c) for c in usar]
            i_id = columnas.index("id")
            return usar, [
                {c: _convertir(fila[i], tipos[tabla][c]) for c, i in zip(usar, indices)}
                for fila in filas if solo_ids is None or str(fila[i_id]) in solo_ids
            ]

        subidas = 0
        if cambios is None:
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in TABLAS)
                              + " RESTART IDENTITY CASCADE"))
            for tabla in TABLAS:
                if tabla not in datos:
                    continue
                _, convertidas = filas_para(tabla)
                destino = db.metadata.tables[tabla]
                for inicio in range(0, len(convertidas), LOTE):
                    conn.execute(destino.insert(), convertidas[inicio:inicio + LOTE])
                subidas += len(convertidas)
        else:
            # hijos antes que padres al borrar, padres antes que hijos al subir
            for tabla in reversed(TABLAS):
                if tabla in cambios and cambios[tabla][1]:
                    destino = db.metadata.tables[tabla]
                    ids = [int(i) for i in cambios[tabla][1]]
                    for inicio in range(0, len(ids), LOTE):
                        conn.execute(destino.delete().where(
                            destino.c.id.in_(ids[inicio:inicio + LOTE])))
                    subidas += len(ids)
            for tabla in TABLAS:
                if tabla not in cambios or not cambios[tabla][0] or tabla not in datos:
                    continue
                usar, convertidas = filas_para(tabla, set(cambios[tabla][0]))
                destino = db.metadata.tables[tabla]
                stmt = pg_insert(destino)
                stmt = stmt.on_conflict_do_update(
                    index_elements=[destino.c.id],
                    set_={c: stmt.excluded[c] for c in usar if c != "id"})
                for inicio in range(0, len(convertidas), LOTE):
                    conn.execute(stmt, convertidas[inicio:inicio + LOTE])
                subidas += len(convertidas)

        # Para que el visor sepa qué tan frescos son los datos. Va en la misma
        # transacción: solo queda registrada una subida que terminó bien.
        _crear_sync_info(conn)
        conn.execute(text("""
            INSERT INTO sync_info (id, subido_en, visto_en, filas) VALUES (1, :t, :t, :n)
            ON CONFLICT (id) DO UPDATE SET subido_en = EXCLUDED.subido_en,
                visto_en = EXCLUDED.visto_en, filas = EXCLUDED.filas
        """), {"t": datetime.now(), "n": sum(local.values())})

    return {"modo": "completa" if cambios is None else "cambios", "filas": subidas}


def _crear_sync_info(conn):
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS sync_info (
            id INTEGER PRIMARY KEY, subido_en TIMESTAMP, visto_en TIMESTAMP,
            filas INTEGER)
    """))
    conn.execute(text("ALTER TABLE sync_info ADD COLUMN IF NOT EXISTS visto_en TIMESTAMP"))


def _latido(neon_url: str) -> None:
    """Sin cambios que subir: solo avisa a Neon que el almacén sigue conectado,
    así el visor no cree que la PC está apagada. Es una sola fila."""
    motor = _motor_neon(neon_url)
    try:
        with motor.begin() as conn:
            _crear_sync_info(conn)
            conn.execute(text("""
                INSERT INTO sync_info (id, visto_en) VALUES (1, :t)
                ON CONFLICT (id) DO UPDATE SET visto_en = EXCLUDED.visto_en
            """), {"t": datetime.now()})
    finally:
        motor.dispose()


def sincronizar(neon_url: str | None = None, forzar: bool = False) -> dict:
    """Sube la base local a Neon. Nunca lanza excepción: devuelve el estado.

    estado["resultado"] es 'ok', 'sin_cambios', 'ocupado', 'no_configurada'
    o 'error' (con estado["error"] explicando por qué).
    """
    if not neon_url and not configurada():
        return {"resultado": "no_configurada"}
    neon_url = neon_url or config.neon_database_url()
    if not neon_url or not db.IS_SQLITE:
        return {"resultado": "no_configurada"}
    if not _lock.acquire(blocking=False):
        return {"resultado": "ocupado"}

    ahora = datetime.now().isoformat(timespec="seconds")
    try:
        with tempfile.TemporaryDirectory(dir=_dir_sync()) as tmp:
            foto = Path(tmp) / "foto.db"
            tomar_foto(config.get_database_path(), foto)
            datos = leer_foto(foto)

        nuevas = firmas(datos)
        previas = None if forzar else _leer_subido()
        cambios = diferencias(previas, nuevas) if previas is not None else None
        previo = leer_estado()
        if cambios == {}:
            visto = previo.get("ultimo_latido") or previo.get("ultima_ok")
            if not visto or (datetime.now() - datetime.fromisoformat(visto)
                             ).total_seconds() >= LATIDO_MIN * 60:
                _latido(neon_url)
                return _guardar_estado(resultado="sin_cambios", ultimo_intento=ahora,
                                       ultimo_latido=ahora, error=None)
            return _guardar_estado(resultado="sin_cambios", ultimo_intento=ahora,
                                   error=None)

        inicio = time.monotonic()
        motor = _motor_neon(neon_url)
        try:
            subida = _subir(motor, datos, cambios, previas)
        finally:
            motor.dispose()
        # recién ahora, con la transacción confirmada: si la app se cierra
        # antes, la próxima vez se vuelven a subir esos cambios (no pasa nada)
        _guardar_subido(nuevas)

        segundos = round(time.monotonic() - inicio, 1)
        log.info("Neon sincronizado (%s): %s filas en %ss",
                 subida["modo"], subida["filas"], segundos)
        return _guardar_estado(resultado="ok", ultimo_intento=ahora, ultima_ok=ahora,
                               modo=subida["modo"], filas=subida["filas"],
                               segundos=segundos, error=None)
    except Exception as e:
        # Sin internet, Neon dormido, etc. La base local no se tocó y Neon
        # quedó como estaba (rollback); se reintenta más tarde.
        log.warning("No se pudo sincronizar con Neon: %s", e)
        return _guardar_estado(resultado="error", ultimo_intento=ahora,
                               error=_mensaje_corto(e))
    finally:
        _lock.release()


def _mensaje_corto(e: Exception) -> str:
    texto = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if "could not translate host name" in texto or "timeout expired" in texto \
            or "Connection refused" in texto or "Network is unreachable" in texto:
        return "sin conexión a internet"
    # Nunca mostrar la URL con la contraseña.
    url = config.neon_database_url() or ""
    if url:
        texto = texto.replace(url, "NEON_DATABASE_URL")
    return texto[:200]


# ======================================================
# Programación automática
# ======================================================
def _minutos_config() -> int:
    try:
        return max(int(os.getenv("NEON_SYNC_MINUTOS", str(MINUTOS_DEFECTO))), 0)
    except ValueError:
        return MINUTOS_DEFECTO


def horas_config(valor: str | None = None) -> list[tuple[int, int]]:
    """'13:00, 19:30' -> [(13, 0), (19, 30)]. Ignora lo que no se entienda."""
    valor = os.getenv("NEON_SYNC_HORAS", "") if valor is None else valor
    horas = []
    for parte in valor.split(","):
        try:
            h, m = parte.strip().split(":")
            if 0 <= int(h) < 24 and 0 <= int(m) < 60:
                horas.append((int(h), int(m)))
        except ValueError:
            continue
    return horas


def toca_ahora(ahora: datetime, ultima: datetime | None, minutos: int,
               horas: list, fallo_previo: bool) -> bool:
    """Decide si corresponde sincronizar en este momento."""
    if ultima is None:
        return True
    pasaron = (ahora - ultima).total_seconds() / 60
    if fallo_previo and pasaron >= REINTENTO_MIN:
        return True
    if minutos and pasaron >= minutos:
        return True
    for h, m in horas:
        objetivo = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
        if ultima < objetivo <= ahora:
            return True
    return False


def avisar_cambio() -> None:
    """La app guardó algo: subirlo enseguida (lo usa app.py tras cada POST)."""
    if _hilo is not None:
        _cambio.set()


def _esperar_que_se_calme() -> None:
    """Una venta guarda varias cosas seguidas (venta, deuda, log...): se
    espera a que pasen unos segundos sin avisos para subir todo junto."""
    inicio = time.monotonic()
    while time.monotonic() - inicio < ESPERA_MAX:
        _cambio.clear()
        if not _cambio.wait(ESPERA_AGRUPAR):
            return


def _bucle():
    minutos, horas = _minutos_config(), horas_config()
    ultima, fallo = None, False
    while True:
        # al abrir la app sube enseguida; después, cada 30 s mira si hay avisos
        avisado = _cambio.wait(30 if ultima else 0)
        if avisado:
            _esperar_que_se_calme()
        ahora = datetime.now()
        if avisado or toca_ahora(ahora, ultima, minutos, horas, fallo):
            estado = sincronizar()
            ultima = ahora
            fallo = estado.get("resultado") == "error"


def iniciar_automatica() -> bool:
    """Arranca el hilo de sincronización si hay algo que programar."""
    global _hilo
    if not configurada() or (_hilo and _hilo.is_alive()):
        return False
    _hilo = threading.Thread(target=_bucle, name="sync-neon", daemon=True)
    _hilo.start()
    return True
