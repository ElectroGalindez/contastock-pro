"""
Sube a Neon solo las ventas de un día, sin tocar el resto.

Es distinto de `migrar_local_a_neon.py`: ese vacía y recarga todo. Acá solo se
inserta lo que falta, así que se puede usar a diario para ir dejando Neon al día
con lo que se factura en la máquina.

No copia las ventas sueltas: cada venta arrastra lo que necesita para que en
Neon no quede nada roto.

- Los clientes que las ventas referencian y todavía no están en Neon. Sin esto
  Postgres las rechaza con "violates foreign key constraint". Passa siempre que
  se hayan cargado clientes nuevos en el día.
- Las deudas y sus detalles de las ventas pendientes. Una venta con pagado <
  total existe porque tiene deuda: copiarla sin la deuda deja una venta
  "Pendiente" que el módulo de deudas no muestra nunca.
- Los logs del día, para que el historial de acciones no tenga un hueco.

Uso:
    python3 scripts/sincronizar_ventas_del_dia.py --dry-run
    python3 scripts/sincronizar_ventas_del_dia.py
    python3 scripts/sincronizar_ventas_del_dia.py --fecha 2026-09-26
    python3 scripts/sincronizar_ventas_del_dia.py --actualizar
"""
import argparse
import sys
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(RAIZ / ".env")

from sqlalchemy import (  # noqa: E402
    MetaData, Table, bindparam, create_engine, insert, inspect, select, text,
)
from sqlalchemy.pool import NullPool  # noqa: E402

from backend import config, db  # noqa: E402
from scripts.migrar_local_a_neon import (  # noqa: E402
    columnas_generadas, convertir_fila, nombre, resetting_sequences,
)



def dia_por_defecto(origen):
    """El día de hoy según la base local, no el de la máquina.

    El reloj del servidor puede estar corrido y las fechas de venta son lo que
    manda: si el equipo facturó hasta las 23h y la máquina ya pasó la medianoche,
    "hoy" para la migración es el día de las ventas.
    """
    with origen.connect() as conn:
        return conn.execute(text("SELECT date('now','localtime')")).scalar()


def ids_que_existen(destino, tabla, ids, esquema):
    if not ids:
        return set()
    with destino.connect() as conn:
        filas = conn.execute(
            text(f"SELECT id FROM {nombre(esquema, tabla)} WHERE id = ANY(:ids)"),
            {"ids": list(ids)}).scalars().all()
    return set(filas)


def tipos_de_columnas(destino, tabla, esquema):
    return {c["name"]: c["type"] for c in inspect(destino).get_columns(
        tabla, schema=esquema)}


def traer(origen, tabla, ids, destino, esquema, generadas):
    """Lee de la base local las filas pedidas y las deja listas para Neon.

    Las columnas generadas se dejan fuera del dict: si aparecen en el INSERT,
    Postgres las rechaza aunque el valor sea NULL. Ver columnas_generadas().
    """
    if not ids:
        return [], []
    tabla_local = Table(tabla, MetaData(), autoload_with=origen)
    tipos = tipos_de_columnas(destino, tabla, esquema)
    omitidas = generadas.get(tabla, set())
    comunes = [c.name for c in db.metadata.tables[tabla].columns
               if c.name in tabla_local.c and c.name in tipos
               and c.name not in omitidas]
    with origen.connect() as conn:
        filas = [dict(r) for r in conn.execute(
            select(*[tabla_local.c[c] for c in comunes])
            .where(tabla_local.c.id.in_(ids))).mappings().all()]
    salida, problemas = [], []
    for fila in filas:
        nueva, avisos = convertir_fila(fila, {c: tipos[c] for c in comunes})
        if avisos:
            problemas.append((tabla, avisos))
        salida.append(nueva)
    return salida, problemas


def filas_por_columna(origen, tabla, columna, valores):
    """Filas de la base local cuyo campo está en la lista.

    SQLite no entiende =ANY(:lista) como Postgres, así que la lista se pasa con
    un bindparam que se expande a los ? que los dos motores sí entienden.
    """
    if not valores:
        return []
    tabla_local = Table(tabla, MetaData(), autoload_with=origen)
    with origen.connect() as conn:
        return [dict(r) for r in conn.execute(
            select(tabla_local)
            .where(tabla_local.c[columna].in_(
                bindparam("vals", expanding=True)))
            .order_by(tabla_local.c.id),
            {"vals": list(valores)}).mappings().all()]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Subir a Neon solo las ventas de un día")
    parser.add_argument("--neon", default=None)
    parser.add_argument("--sqlite", default=None)
    parser.add_argument("--fecha", default=None,
                        help="Día a sincronizar, YYYY-MM-DD (por defecto, hoy)")
    parser.add_argument("--actualizar", action="store_true",
                        help="Sobrescribir ventas que ya están en Neon "
                             "(por defecto se saltan)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--esquema", default="public",
                        help="Esquema de Postgres destino (por defecto public)")
    args = parser.parse_args()
    esquema = args.esquema

    neon_url = args.neon or config.neon_database_url()
    if not neon_url:
        print("ERROR: no hay NEON_DATABASE_URL configurada en .env")
        return 1

    origen = create_engine(args.sqlite or config.sqlite_database_url(), future=True)
    # NullPool: este script no debe dejar sesión abierta en el pooler de Neon.
    # schema_translate_map hace que los INSERT del metadata lleven el esquema
    # delante. Sin esto, db.metadata.tables["ventas"].insert() genera SQL sin
    # calificar y el motor lo resuelve con el search_path de la conexión, o sea
    # "public": el script escribía en public aunque se le pidiera otro esquema.
    destino = create_engine(
        neon_url, future=True, poolclass=NullPool,
        execution_options={"schema_translate_map": {None: args.esquema}})

    with destino.connect() as conn:
        conn.execute(text("SELECT 1"))

    dia = args.fecha or dia_por_defecto(origen)
    print(f"Dia a sincronizar: {dia}")
    print(f"Origen : {origen.url}")
    print(f"Destino: ...{neon_url.split('@')[-1]}")
    if args.dry_run:
        print("MODO SIMULADO: no se escribe nada")
    print()

    # ---- ventas del día ----
    with origen.connect() as conn:
        ventas = [dict(r) for r in conn.execute(text("""
            SELECT * FROM ventas
            WHERE date(fecha) = :dia ORDER BY id
        """), {"dia": dia}).mappings().all()]

    if not ventas:
        print(f"No hay ventas con fecha {dia} en la base local. Nada que hacer.")
        return 0

    ids_ventas = [v["id"] for v in ventas]
    total = sum(float(v.get("total") or 0) for v in ventas)
    print(f"Ventas del {dia}: {len(ventas)} por Gs. {total:,.0f}")

    # ---- qué falta en Neon ----
    ya_en_neon = ids_que_existen(destino, "ventas", ids_ventas, esquema)
    a_subir = [v for v in ventas if v["id"] not in ya_en_neon]
    if args.actualizar:
        a_subir = list(ventas)
    print(f"  ya están en Neon: {len(ya_en_neon)}   a subir: {len(a_subir)}")

    # No se corta acá si no hay ventas nuevas. Igual hay que seguir: puede pasar
    # que la venta esté pero le falten su deuda o sus detalles, que es como
    # quedó public cuando el INSERT de deudas_detalle chocó contra la columna
    # generada. Y las secuencias hay que ajustarlas siempre, o la próxima venta
    # en Neon sale con un id que ya existe.

    # ---- clientes que necesitan ----
    # Se miran TODAS las ventas del día, no solo las que faltan: así el script
    # sirve también para completar una sincronización a medias.
    clientes = set()
    for v in ventas:
        for campo in ("cliente_id", "gestor_id"):
            if v.get(campo) is not None:
                clientes.add(int(v[campo]))
    faltan_clientes = sorted(clientes - ids_que_existen(destino, "clientes", clientes, esquema))

    # ---- deudas de las ventas que suben ----
    deudas = filas_por_columna(origen, "deudas", "venta_id", ids_ventas)
    ids_deudas = [d["id"] for d in deudas]
    ya_deudas = ids_que_existen(destino, "deudas", ids_deudas, esquema)
    a_subir_deudas = [d for d in deudas if d["id"] not in ya_deudas]

    detalles = filas_por_columna(origen, "deudas_detalle", "deuda_id", ids_deudas)
    ids_detalles = [d["id"] for d in detalles]
    ya_detalles = ids_que_existen(destino, "deudas_detalle", ids_detalles, esquema)
    a_subir_detalles = [d for d in detalles if d["id"] not in ya_detalles]

    print(f"\nClientes referenciados: {len(clientes)}   faltan en Neon: "
          f"{len(faltan_clientes)}")
    for i in faltan_clientes:
        with origen.connect() as conn:
            n = conn.execute(text("SELECT nombre FROM clientes WHERE id=:i"),
                             {"i": i}).scalar()
        print(f"  cliente {i}: {n}")
    print(f"Deudas: {len(a_subir_deudas)} a subir (de {len(deudas)} de esas ventas)")
    print(f"Detalles de deuda: {len(a_subir_detalles)} a subir")
    pendientes = [v for v in a_subir if float(v.get("pagado") or 0) < float(v.get("total") or 0)]
    print(f"Ventas pendientes entre las que suben: {len(pendientes)}")

    if args.dry_run:
        print(f"\nSimulación: se insertarían {len(faltan_clientes)} clientes, "
              f"{len(a_subir)} ventas, {len(a_subir_deudas)} deudas y "
              f"{len(a_subir_detalles)} detalles. Sin cambios.")
        return 0

    # El orden importa por las claves foráneas: clientes antes que ventas,
    # ventas antes que deudas, deudas antes que sus detalles. Por eso no hace
    # falta tocar las claves: cada fila llega con lo que referencia ya puesto.
    problemas = []
    plan = [
        ("clientes", faltan_clientes),
        ("ventas", [v["id"] for v in a_subir]),
        ("deudas", [d["id"] for d in a_subir_deudas]),
        ("deudas_detalle", [d["id"] for d in a_subir_detalles]),
    ]

    print("\nSubiendo:")
    generadas = columnas_generadas(destino, esquema)
    for tabla, ids in plan:
        if not ids:
            print(f"  {tabla:16} nada que subir")
            continue
        filas, avisos = traer(origen, tabla, ids, destino, esquema, generadas)
        problemas.extend(avisos)
        destino_tabla = db.metadata.tables[tabla]
        for i in range(0, len(filas), 500):
            with destino.begin() as conn:
                conn.execute(insert(destino_tabla), filas[i:i + 500])
        nota = (f"  (sin {', '.join(sorted(generadas.get(tabla, [])))}, "
                f"las calcula Postgres)") if generadas.get(tabla) else ""
        print(f"  {tabla:16} {len(filas)} filas{nota}")

    print("\nAjustando secuencias:")
    for s in resetting_sequences(destino, [t for t, _ in plan], esquema):
        print(f"  {s}")

    # ---- verificación ----
    print("\nVerificando:")
    fallos = []
    for tabla, ids in plan:
        if not ids:
            continue
        presentes = ids_que_existen(destino, tabla, ids, esquema)
        faltan = sorted(set(ids) - presentes)
        if faltan:
            fallos.append(f"{tabla}: no llegaron {faltan[:10]}")
            print(f"  [{'FALLA' if faltan else 'ok':5}] {tabla:16} "
                  f"{len(presentes)}/{len(ids)}")
        else:
            print(f"  [ok   ] {tabla:16} {len(presentes)}/{len(ids)}")

    with destino.connect() as conn:
        total_neon = conn.execute(text(
            f"SELECT COUNT(*) FROM {nombre(esquema, 'ventas')} "
            "WHERE date(fecha) = :dia"), {"dia": dia}).scalar()
    print(f"\nVentas con fecha {dia} en Neon ahora: {total_neon}")

    if problemas:
        print(f"\nAVISO: {len(problemas)} filas con valores que no se pudieron convertir.")
        return 2
    if fallos:
        print("\nFALLAS:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("\nListo. Neon ya tiene las ventas del día.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
