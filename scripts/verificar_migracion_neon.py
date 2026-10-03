"""
Verifica el camino de escritura del migrador en un PostgreSQL real, sin tocar
los datos de la app.

Crea un esquema desechable (`migr_test`), copia ahí la base local con las mismas
funciones que usa `migrar_local_a_neon.py`, comprueba que los tipos quedaron
bien guardados (jsonb, boolean, numeric, timestamp) que las secuencias siguen
sirviendo ids correctos, y al final borra el esquema.

Es lo que no se puede comprobar con el --dry-run, que solo lee y convierte en
memoria. Necesita NEON_DATABASE_URL.

    python scripts/verificar_migracion_neon.py
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(RAIZ / ".env")

from sqlalchemy import (  # noqa: E402
    MetaData, Table, create_engine, event, inspect, select, text,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from backend import config, db  # noqa: E402
from scripts.migrar_local_a_neon import (  # noqa: E402
    ORDER, ProblemaConversion, columnas_generadas, convertir_fila,
    quitar_claves_foraneas, reponer_claves_foraneas, secuencia_de,
)

ESQUEMA = "migr_test"


def main() -> int:
    url = config.neon_database_url()
    if not url:
        print("ERROR: falta NEON_DATABASE_URL")
        return 1

    admin = create_engine(url, future=True)
    local = create_engine(config.sqlite_database_url(), future=True)
    publico = create_engine(url, future=True)

    print(f"Probando contra ...{url.split('@')[-1]}")
    print(f"Esquema de prueba: {ESQUEMA} (se borra al terminar)\n")

    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE"))
        conn.execute(text(f"CREATE SCHEMA {ESQUEMA}"))

    # Motor apuntando solo al esquema de prueba, para no escribir en public.
    # El pooler de Neon no admite search_path en el paquete de arranque, asi
    # que se fija despues de conectar.
    #
    # Ojo con el "close": aunque la conexion local se cierre, detras queda la
    # sesion del servidor en el pooler de Neon, y un search_path sin resetear
    # se lo hereda a las siguientes. Por eso se hace RESET ALL al soltar.
    destino = create_engine(url, future=True, poolclass=NullPool)

    @event.listens_for(destino, "connect")
    def _usar_esquema_de_prueba(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {ESQUEMA}")
        cursor.close()

    @event.listens_for(destino, "close")
    def _restablecer(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("RESET ALL")
        cursor.close()

    # create_all no recibe schema: usa el search_path que se puso al conectar,
    # asi que las tablas se crean en el esquema de prueba.
    db.metadata.create_all(destino)

    # El metadata declara `Column("monto", Numeric)` a secas, pero en Neon
    # deudas_detalle.monto y ventas_detalle.monto son columnas generadas
    # (cantidad * precio_unitario). Un esquema de prueba con el metadata pelado
    # NO las crea, y asi el verificador pasaria sin detectar que el migrador
    # manda una columna generada en el INSERT y Postgres la rechaza. Se copian
    # tal cual desde el catálogo de public para que la prueba sea fiel.
    generadas_neon = columnas_generadas(publico, "public")
    for tabla, columnas in generadas_neon.items():
        with destino.begin() as conn:
            for columna, expresion in sorted(columnas.items()):
                conn.execute(text(f"ALTER TABLE {ESQUEMA}.{tabla} DROP COLUMN {columna}"))
                conn.execute(text(
                    f"ALTER TABLE {ESQUEMA}.{tabla} ADD COLUMN {columna} numeric "
                    f"GENERATED ALWAYS AS ({expresion}) STORED"))
    if generadas_neon:
        print("Columnas generadas copiadas de public: "
              + ", ".join(f"{t}.{c}" for t, cs in sorted(generadas_neon.items())
                          for c in sorted(cs)))
    print("Esquema creado. Copiando tablas:\n")

    inspector = inspect(destino)
    origen_meta = MetaData()
    fallos, copiadas = [], {}

    # Mismo ciclo que el migrador: la base local tiene filas huerfanas y
    # Postgres las rechaza con las claves foráneas puestas.
    fks = quitar_claves_foraneas(destino, ESQUEMA)
    print(f"Claves foráneas quitadas: {len(fks)}\n")

    for nombre in ORDER:
        if nombre not in db.metadata.tables:
            continue
        tabla_origen = Table(nombre, origen_meta, autoload_with=local)
        tipos = {c["name"]: c["type"] for c in inspector.get_columns(nombre, schema=ESQUEMA)}
        # Las columnas generadas no van en el INSERT: las calcula Postgres.
        omitidas = generadas_neon.get(nombre, {})
        comunes = [c.name for c in db.metadata.tables[nombre].columns
                   if c.name in tabla_origen.c and c.name in tipos
                   and c.name not in omitidas]
        if not comunes:
            continue

        with local.connect() as conn:
            filas = [dict(r) for r in conn.execute(
                select(*[tabla_origen.c[c] for c in comunes])).mappings().all()]

        listas = []
        for fila in filas:
            nueva, problemas = convertir_fila(fila, {c: tipos[c] for c in comunes})
            if problemas:
                fallos.append(f"{nombre}: {problemas}")
            listas.append(nueva)

        if listas:
            tabla_destino = db.metadata.tables[nombre]
            for i in range(0, len(listas), 500):
                with destino.begin() as conn:
                    conn.execute(tabla_destino.insert(), listas[i:i + 500])

        copiadas[nombre] = len(listas)
        extra = (f"  (sin {', '.join(sorted(omitidas))}, generadas)"
                 if omitidas else "")
        print(f"  {nombre:18} {len(listas):>6} filas{extra}")

    print("\nComprobaciones:")

    # 0. Las claves foráneas vuelven a estar y siguen controlando inserts nuevos
    reponer_claves_foraneas(destino, fks, ESQUEMA)
    if fks:
        print(f"  [{'ok' if True else 'FALLA':5}] {len(fks)} claves foráneas repuestas")
    con_destino_prueba = create_engine(url, future=True, poolclass=NullPool)

    @event.listens_for(con_destino_prueba, "connect")
    def _otra_prueba(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute(f"SET search_path TO {ESQUEMA}")
        cursor.close()

    if fks:
        # tiene que rechazar un insert que rompa una FK
        rechazada = False
        try:
            with con_destino_prueba.begin() as conn:
                conn.execute(text(
                    f'INSERT INTO {ESQUEMA}.ventas (id, cliente_id, total) '
                    "VALUES (999999, 999999, 0)"))
        except Exception:
            rechazada = True
        if not rechazada:
            fallos.append("una clave foranea repuesta no rechazo una fila invalida")
        print(f"  [{'ok' if rechazada else 'FALLA':5}] "
              f"una clave repuesta sigue rechazando filas invalidas")
    con_destino_prueba.dispose()

    # 1. Recuento de filas
    for nombre, esperadas in copiadas.items():
        with destino.connect() as conn:
            reales = conn.execute(text(f"SELECT COUNT(*) FROM {ESQUEMA}.{nombre}")).scalar()
        estado = "ok" if reales == esperadas else "FALLA"
        if reales != esperadas:
            fallos.append(f"{nombre}: {reales} filas != {esperadas}")
        print(f"  [{estado:5}] {nombre:18} {reales:>6} filas")

    # 2. jsonb: tiene que seguir siendo un array, no un texto.
    # La tabla de prueba nace como json y la de Neon como jsonb, por eso el cast.
    with destino.connect() as conn:
        r = conn.execute(text(
            f"SELECT productos_vendidos, "
            "jsonb_typeof(productos_vendidos::jsonb) "
            f"FROM {ESQUEMA}.ventas WHERE productos_vendidos IS NOT NULL LIMIT 1")).first()
    if r:
        ok = r[1] == "array" and isinstance(r[0], (list, dict))
        print(f"  [{'ok' if ok else 'FALLA':5}] ventas.productos_vendidos es un array JSON")
        if not ok:
            fallos.append(f"productos_vendidos quedo como {r[1]}: {r[0]!r}")

    # 3. boolean: tiene que quedar true/false, no 0/1
    with destino.connect() as conn:
        tipos_b = conn.execute(text(
            f"SELECT DISTINCT pg_typeof(activo)::text FROM {ESQUEMA}.usuarios")).scalars().all()
    ok = tipos_b == ["boolean"]
    print(f"  [{'ok' if ok else 'FALLA':5}] usuarios.activo es boolean ({tipos_b})")
    if not ok:
        fallos.append(f"usuarios.activo quedo como {tipos_b}")

    # 4. timestamp: tiene que quedar como fecha, no texto
    with destino.connect() as conn:
        r = conn.execute(text(
            f"SELECT fecha, pg_typeof(fecha)::text FROM {ESQUEMA}.ventas "
            "WHERE fecha IS NOT NULL LIMIT 1")).first()
    ok = r and r[1] == "timestamp without time zone"
    print(f"  [{'ok' if ok else 'FALLA':5}] ventas.fecha es timestamp ({r[1] if r else 'sin datos'})")
    if not ok:
        fallos.append(f"ventas.fecha quedo como {r[1] if r else 'vacio'}")

    # 5. numeric: tiene que conservar los decimales
    with destino.connect() as conn:
        r = conn.execute(text(
            f"SELECT total, pg_typeof(total)::text FROM {ESQUEMA}.ventas LIMIT 1")).first()
    ok = r and r[1] == "numeric"
    print(f"  [{'ok' if ok else 'FALLA':5}] ventas.total es numeric ({r[1] if r else 'sin datos'})")
    if not ok:
        fallos.append(f"ventas.total quedo como {r[1] if r else 'vacio'}")

    # 6. secuencias: el proximo id no puede chocar con uno copiado
    for tabla in ("ventas", "clientes", "deudas", "productos"):
        secuencia = secuencia_de(destino, tabla, ESQUEMA)
        if not secuencia:
            continue
        with destino.begin() as conn:
            maximo = conn.execute(text(
                f"SELECT MAX(id) FROM {ESQUEMA}.{tabla}")).scalar() or 0
            conn.execute(text("SELECT setval(:s, :v, true)"),
                         {"s": secuencia, "v": int(maximo)})
        with destino.connect() as conn:
            conn.execute(text("SELECT nextval(:s)"), {"s": secuencia})
            siguiente = conn.execute(text("SELECT nextval(:s)"), {"s": secuencia}).scalar()
        ok = siguiente > maximo
        print(f"  [{'ok' if ok else 'FALLA':5}] secuencia {tabla:10} "
              f"max {maximo} -> siguiente {siguiente}")
        if not ok:
            fallos.append(f"secuencia {tabla} devuelve {siguiente}, deberia ser > {maximo}")

    with admin.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {ESQUEMA} CASCADE"))
    print(f"\nEsquema {ESQUEMA} borrado. Los datos de Neon no se tocaron.")

    if fallos:
        print(f"\n{len(fallos)} FALLAS:")
        for f in fallos[:20]:
            print(f"  - {f}")
        return 1
    print("\nTodo correcto. El migrador escribe bien en PostgreSQL.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
