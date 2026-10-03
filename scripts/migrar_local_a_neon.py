"""
Sube la base SQLite local a Neon (PostgreSQL). Inverso de
`migrar_neon_a_local.py`.

La app trabaja contra SQLite local (ver `backend/config.py`). Cuando se quiere
publicar en Neon, este script copia la base local completa, dejando Neon como
espejo exacto de lo que se está usando.

Ojo: reemplaza TODOS los datos de Neon por los de la base local. La base local
pasa a ser la fuente de verdad. Pide confirmación y avisa cuántas filas va a
borrar antes de hacerlo.

Uso:
    python scripts/migrar_local_a_neon.py --dry-run   # solo informa, no escribe
    python scripts/migrar_local_a_neon.py             # pide confirmación
    python scripts/migrar_local_a_neon.py --si        # sin preguntar
    python scripts/migrar_local_a_neon.py --tablas clientes,ventas

Diferencias entre SQLite y PostgreSQL que este script resuelve (SQLAlchemy
inserta las filas sin convertirlas, y Postgres es más estricto):

- BOOLEAN: SQLite guarda 0/1, Postgres exige true/false.
- NUMERIC: hay que mandar Decimal, no float.
- TIMESTAMP: SQLite devuelve texto 'YYYY-MM-DD HH:MM:SS'.
- JSONB: en SQLite está guardado como texto.
- Secuencias: al copiar el id a mano, `ventas_id_seq` queda atrás y la
  siguiente venta insertada en Neon choca por id duplicado.
"""
import argparse
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from dotenv import load_dotenv  # noqa: E402
load_dotenv(RAIZ / ".env")

from sqlalchemy import (  # noqa: E402
    Boolean, Date, DateTime, Integer, JSON, MetaData, Numeric, Table, create_engine,
    inspect, select, text,
)
from sqlalchemy.exc import NoSuchTableError  # noqa: E402

from backend import config, db  # noqa: E402

# Orden de copia respetando dependencias (padres antes que hijos).
ORDER = [
    "usuarios", "categorias", "productos", "clientes",
    "ventas", "ventas_detalle", "deudas", "deudas_detalle",
    "pagos_deudas", "logs", "auditoria",
]

LOTE = 500


# ======================================================
# Conversión de tipos SQLite -> PostgreSQL
# ======================================================
class ProblemaConversion(Exception):
    pass


def _a_bool(valor):
    if isinstance(valor, bool):
        return valor
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return bool(valor)
    texto = str(valor).strip().lower()
    if texto in ("1", "true", "t", "yes", "si", "s"):
        return True
    if texto in ("0", "false", "f", "no"):
        return False
    raise ProblemaConversion(f"no se pudo interpretar como booleano: {valor!r}")


def _a_decimal(valor):
    if valor is None or isinstance(valor, Decimal):
        return valor
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError):
        raise ProblemaConversion(f"no se pudo interpretar como número: {valor!r}")


def _a_datetime(valor):
    if valor is None or isinstance(valor, datetime):
        return valor
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day)
    texto = str(valor).strip()
    if not texto:
        return None
    try:
        return datetime.fromisoformat(texto)
    except ValueError:
        for formato in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                continue
    raise ProblemaConversion(f"no se pudo interpretar como fecha: {valor!r}")


def _a_json(valor):
    """SQLite guarda el JSON como texto; Postgres jsonb quiere el objeto."""
    if valor is None or not isinstance(valor, str):
        return valor
    import json
    texto = valor.strip()
    if not texto:
        return None
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        raise ProblemaConversion(f"el JSON guardado no se puede leer: {valor[:60]!r}")


CONVERSORES = {
    Boolean: _a_bool,
    Numeric: _a_decimal,
    DateTime: _a_datetime,
    Date: _a_datetime,
    JSON: _a_json,
    Integer: lambda v: None if v is None else int(v),
}


def convertir(valor, tipo_destino):
    """Lleva un valor de SQLite al tipo que la columna de Neon espera."""
    for clase, funcion in CONVERSORES.items():
        if isinstance(tipo_destino, clase):
            return funcion(valor)
    return valor


def convertir_fila(fila, columnas):
    """convierte={nombre_de_columna: tipo_destino}"""
    salida, problemas = {}, []
    for nombre, tipo in columnas.items():
        valor = fila.get(nombre)
        try:
            salida[nombre] = convertir(valor, tipo)
        except ProblemaConversion as e:
            problemas.append(f"{nombre}={valor!r} ({e})")
            salida[nombre] = None
    return salida, problemas


# ======================================================
# Esquema
# ======================================================
def agregar_columnas_faltantes(destino, esquema="public"):
    """Crea en Neon las columnas que el metadata tiene y la tabla no.

    `metadata.create_all()` solo crea tablas nuevas: no agrega columnas a las
    que ya existen, así que sin esto `ventas.gestor_id` o `clientes.es_gestor`
    quedarían sin copiar y el panel de gestores no funcionaría en Neon.
    """
    inspector = inspect(destino)
    existentes = set(inspector.get_table_names(schema=esquema))
    agregadas = []

    for nombre in db.metadata.tables:
        if nombre not in existentes:
            continue
        actuales = {c["name"] for c in inspector.get_columns(nombre, schema=esquema)}
        for columna in db.metadata.tables[nombre].columns:
            if columna.name in actuales:
                continue
            tipo = columna.type.compile(destino.dialect)
            default = ""
            if columna.server_default is not None:
                default = f" DEFAULT {_texto_default(columna.server_default)}"
            con_destino(destino, f'ALTER TABLE {nombre(esquema, nombre)} '
                                 f'ADD COLUMN "{columna.name}" {tipo}{default}')
            agregadas.append(f"{nombre}.{columna.name}")
    return agregadas


def _texto_default(server_default):
    """El valor por defecto del metadata, tal como lo escribe Postgres.

    Viene como texto ('1', 'false'), así que se pasa tal cual. Booleanos con
    0/1 habrian sido rechazados por Postgres, pero el schema ya usa false/true.
    """
    valor = server_default.arg
    return str(getattr(valor, "text", valor))


def con_destino(destino, sql, params=None):
    with destino.begin() as conn:
        return conn.execute(text(sql), params or {})


def nombre(schema, tabla):
    return f'"{schema}"."{tabla}"'


def claves_foraneas(destino, esquema="public"):
    """ definiciones de las claves foráneas que ya tiene Neon.

    Se leen de la base y no del metadata porque lo que importa es lo que esta
    realmente definido, que puede diferir de lo que el metadata dice.
    """
    with destino.connect() as conn:
        filas = conn.execute(text("""
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
            WHERE tc.constraint_type = 'FOREIGN KEY'
              AND tc.table_schema = :s
        """), {"s": esquema}).mappings().all()
    return [dict(f) for f in filas]


def quitar_claves_foraneas(destino, esquema="public"):
    """Saca las FK antes de cargar y devuelve como reponerlas.

    La base local tiene filas huerfanas (ventas cuyo cliente ya no existe, por
    ejemplo) y Postgres las rechaza con "violates foreign key constraint".
    SQLite no se fijo porque las FK se activaron despues de que existieran
    esos datos. La alternativa era perder esas filas, asi que se cargan tal
    cual y las claves se reponen al final como NOT VALID: siguen controlando
    los inserts nuevos, pero no rechazan el historico que ya estaba roto.
    """
    fks = claves_foraneas(destino, esquema)
    for fk in fks:
        con_destino(destino, f"ALTER TABLE {nombre(esquema, fk['table_name'])} "
                             f"DROP CONSTRAINT {fk['constraint_name']}")
    return fks


def reponer_claves_foraneas(destino, fks, esquema="public"):
    for fk in fks:
        con_destino(destino, (
            f"ALTER TABLE {nombre(esquema, fk['table_name'])} "
            f"ADD CONSTRAINT {fk['constraint_name']} "
            f"FOREIGN KEY ({fk['column_name']}) "
            f"REFERENCES {nombre(esquema, fk['tabla_ref'])} ({fk['columna_ref']}) "
            f"ON UPDATE {fk['update_rule']} ON DELETE {fk['delete_rule']} NOT VALID"
        ))


REVISAS_HUERFANOS = [
    ("ventas", "cliente_id", "clientes"),
    ("ventas", "gestor_id", "clientes"),
    ("ventas_detalle", "venta_id", "ventas"),
    ("ventas_detalle", "producto_id", "productos"),
    ("deudas", "cliente_id", "clientes"),
    ("deudas", "venta_id", "ventas"),
    ("deudas_detalle", "deuda_id", "deudas"),
    ("deudas_detalle", "producto_id", "productos"),
    ("pagos_deudas", "deuda_id", "deudas"),
    ("pagos_deudas", "cliente_id", "clientes"),
    ("productos", "categoria_id", "categorias"),
]


def contar_huerfanos(origen):
    """Revisa las referencias rotas de la base local."""
    salida = []
    with origen.connect() as conn:
        tablas = set(inspect(origen).get_table_names())
        for tabla, columna, ref in REVISAS_HUERFANOS:
            if tabla not in tablas or ref not in tablas:
                continue
            n = conn.execute(text(
                f"SELECT COUNT(*) FROM {tabla} t WHERE t.{columna} IS NOT NULL "
                f"AND NOT EXISTS (SELECT 1 FROM {ref} r WHERE r.id = t.{columna})"
            )).scalar() or 0
            if n:
                salida.append((f"{tabla}.{columna} -> {ref}", n))
    return salida


def secuencia_de(destino, tabla, esquema="public"):
    """Nombre de la secuencia de autoincremento de tabla.id, o None."""
    with destino.connect() as conn:
        return conn.execute(text(
            "SELECT pg_get_serial_sequence(:t, 'id')"),
            {"t": nombre(esquema, tabla)}).scalar()


def resetting_sequences(destino, tablas, esquema="public"):
    """Deja cada secuencia en el último id copiado.

    Sin esto, la proxima venta insertada en Neon usa el id 1 de la secuencia y
    choca con una venta que ya existe: error de clave duplicada.
    """
    oks = []
    for tabla in tablas:
        secuencia = secuencia_de(destino, tabla, esquema)
        if not secuencia:
            continue
        with destino.begin() as conn:
            maximo = conn.execute(text(
                f"SELECT COALESCE(MAX(id), 0) FROM {nombre(esquema, tabla)}")).scalar() or 0
            if maximo > 0:
                # true = is_called: el proximo id será maximo + 1
                conn.execute(text("SELECT setval(:s, :v, true)"),
                             {"s": secuencia, "v": int(maximo)})
            else:
                # Tabla vacía. setval(seq, 0, true) revienta con
                # "value 0 is out of bounds for sequence" porque las secuencias
                # arrancan en 1. En vez de eso se deja la secuencia en 1 sin
                # marcar como usada, así el primer id que salga es el 1.
                conn.execute(text("SELECT setval(:s, 1, false)"),
                             {"s": secuencia})
        oks.append(f"{tabla} (id {maximo})")
    return oks


# ======================================================
# Migración
# ======================================================
def columnas_generadas(destino, esquema="public"):
    """Columnas que Postgres calcula solo (GENERATED ALWAYS AS ... STORED).

    En Neon, deudas_detalle.monto y ventas_detalle.monto son
    `cantidad * precio_unitario`. SQLite no las marca como generadas, así que la
    base local sí tiene el valor guardado, pero Postgres rechaza el INSERT si se
    manda la columna: `cannot insert a non-DEFAULT value into column "monto"`,
    incluso cuando el valor es NULL. Hay que dejarlas fuera y dejarlas que las
    calcule la base.

    Se lee del catálogo y no del metadata a propósito: el metadata declara
    `Column("monto", Numeric)` a secas, o sea que create_all sobre una base
    nueva NO genera la columna y la discrepancia solo se ve mirando el
    servidor.

    Devuelve {tabla: {columna: expresion}} para que quien reconstruya el
    esquema pueda copiar la columna tal cual está.
    """
    with destino.connect() as conn:
        filas = conn.execute(text("""
            SELECT table_name, column_name, generation_expression
            FROM information_schema.columns
            WHERE table_schema = :esquema AND is_generated = 'ALWAYS'
        """), {"esquema": esquema}).all()
    generadas = {}
    for tabla, columna, expresion in filas:
        generadas.setdefault(tabla, {})[columna] = expresion
    return generadas


def contar(origen, tabla, esquema=None):
    completa = f'"{esquema}"."{tabla}"' if esquema else tabla
    with origen.connect() as conn:
        return conn.execute(text(f"SELECT COUNT(*) FROM {completa}")).scalar() or 0


def migrar(origen, destino, tablas, dry_run, lote=LOTE, esquema="public"):
    inspector_destino = inspect(destino)
    existentes_destino = set(inspector_destino.get_table_names(schema=esquema))
    origen_meta = MetaData()
    generadas = columnas_generadas(destino, esquema)

    # El TRUNCATE lleva CASCADE porque hay claves foráneas: truncar ventas
    # borra solas ventas_detalle y deudas. Por eso solo tiene sentido con TODAS
    # las tablas; con un subconjunto se avisa abajo.
    if not dry_run and tablas:
        lista = ", ".join(f'"{t}"' for t in tablas)
        con_destino(destino, f"TRUNCATE {lista} RESTART IDENTITY CASCADE")

    resumen, problemas_conversion, migradas = [], [], []

    for nombre in tablas:
        if nombre not in db.metadata.tables:
            continue

        try:
            tabla_origen = Table(nombre, origen_meta, autoload_with=origen)
        except NoSuchTableError:
            print(f"  {nombre:18} (no existe en la base local, se omite)")
            continue

        if nombre not in existentes_destino:
            # Todavía no está en Neon: se creará recién. En el simulado se
            # copian igual, usando los tipos del metadata, para poder ver
            # cuántas filas irían y detectar valores que no se puedan convertir.
            print(f"  {nombre:18} (tabla nueva en Neon)")
            if not dry_run:
                continue
            columnas_destino = {c.name: c.type for c in db.metadata.tables[nombre].columns}
        else:
            columnas_destino = {c["name"]: c["type"]
                                for c in inspector_destino.get_columns(nombre, schema=esquema)}
        omitidas = generadas.get(nombre, set())
        comunes = [c.name for c in db.metadata.tables[nombre].columns
                   if c.name in tabla_origen.c and c.name in columnas_destino
                   and c.name not in omitidas]
        if omitidas:
            print(f"  {nombre:18} ({', '.join(sorted(omitidas))} las calcula Postgres)")
        if not comunes:
            print(f"  {nombre:18} (sin columnas en comun, se omite)")
            continue

        with origen.connect() as conn:
            filas = [dict(r) for r in conn.execute(
                select(*[tabla_origen.c[c] for c in comunes])).mappings().all()]

        tipos = {c: columnas_destino[c] for c in comunes}
        convertidas = []
        for fila in filas:
            nueva, problemas = convertir_fila(fila, tipos)
            if problemas:
                problemas_conversion.append((nombre, problemas))
            convertidas.append(nueva)

        if not dry_run:
            tabla_destino = db.metadata.tables[nombre]
            for inicio in range(0, len(convertidas), lote):
                trozo = convertidas[inicio:inicio + lote]
                if trozo:
                    with destino.begin() as conn:
                        conn.execute(tabla_destino.insert(), trozo)

        resumen.append((nombre, len(filas)))
        migradas.append(nombre)
        print(f"  {nombre:18} {len(filas):>6} filas"
              f"{'  (simulado)' if dry_run else ''}")

    return resumen, migradas, problemas_conversion


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrar la base SQLite local a Neon (PostgreSQL)")
    parser.add_argument("--neon", default=None,
                        help="URL de Neon (por defecto NEON_DATABASE_URL del .env)")
    parser.add_argument("--sqlite", default=None,
                        help="URL de la base local (por defecto la de la app)")
    parser.add_argument("--tablas", default=None,
                        help="Copiar solo estas tablas, separadas por coma")
    parser.add_argument("--lote", type=int, default=LOTE)
    parser.add_argument("--dry-run", action="store_true",
                        help="Informa lo que haría sin escribir nada")
    parser.add_argument("--si", action="store_true",
                        help="No pedir confirmación")
    parser.add_argument("--esquema", default="public",
                        help="Esquema de Postgres destino (por defecto public)")
    args = parser.parse_args()

    neon_url = args.neon or config.neon_database_url()
    if not neon_url:
        print("ERROR: no hay NEON_DATABASE_URL configurada en .env")
        return 1

    local_url = args.sqlite or config.sqlite_database_url()
    ocultado = neon_url.split("@")[-1]
    print(f"Origen : {local_url}")
    print(f"Destino: ...{ocultado}")
    if args.dry_run:
        print("MODO SIMULADO: no se escribe nada\n")
    print()

    origen = create_engine(local_url, future=True)
    destino = create_engine(neon_url, future=True)

    # Conectar primero: si Neon no responde, no seguimos.
    with destino.connect() as conn:
        conn.execute(text("SELECT 1"))

    tablas = [t.strip() for t in args.tablas.split(",")] if args.tablas else list(ORDER)

    print("Estado de Neon antes de migrar:")
    existentes = set(inspect(destino).get_table_names(schema=args.esquema))
    vorher = {t: contar(destino, t, args.esquema) for t in tablas if t in existentes}
    for t in ORDER:
        if t in vorher:
            print(f"  {t:18} {vorher[t]:>6} filas")

    total_borrar = sum(vorher.values())
    if not args.dry_run and total_borrar:
        print(f"\nATENCION: esto borra {total_borrar} filas de Neon y las reemplaza"
              "\npor las de la base local. La base local queda como fuente de verdad.")
        if len(tablas) < len(ORDER):
            print("Ojo: usaste --tablas, y TRUNCATE CASCADE tambien borra las tablas\n"
                  "que se refieran entre si aunque no esten en la lista.")
        if not args.si:
            respuesta = input("\nContinuar? (si/no): ").strip().lower()
            if not respuesta.startswith("s"):
                print("Cancelado.")
                return 1
    print()

    print("Preparando esquema en Neon:")
    if args.dry_run:
        faltantes = [t for t in tablas
                     if t in db.metadata.tables and t not in existentes]
        for t in faltantes:
            print(f"  {t:18} se creara la tabla")
        if not faltantes:
            print("  (todo existe, no hace falta crear nada)")
    else:
        db.metadata.create_all(destino)
        agregadas = agregar_columnas_faltantes(destino, args.esquema)
        for a in agregadas:
            print(f"  columna agregada: {a}")
        if not agregadas:
            print("  (el esquema ya estaba completo)")
    print()

    print("Copiando datos:")
    fks = []
    if not args.dry_run:
        # Las FK se sacan antes de cargar: la base local tiene filas huerfanas
        # y Postgres las rechaza. Se reponen al final como NOT VALID.
        fks = quitar_claves_foraneas(destino, args.esquema)
        print(f"  (se quitaron {len(fks)} claves foraneas durante la carga)")

    resumen, migradas, problemas = migrar(
        origen, destino, tablas, args.dry_run, lote=args.lote, esquema=args.esquema)

    if not args.dry_run:
        if fks:
            reponer_claves_foraneas(destino, fks, args.esquema)
            print(f"  claves foraneas repuestas ({len(fks)}, como NOT VALID)")

        print("\nAjustando secuencias de autoincremento:")
        for s in resetting_sequences(destino, migradas, args.esquema):
            print(f"  {s}")

        print("\nVerificando:")
        for tabla, esperadas in resumen:
            reales = contar(destino, tabla, args.esquema)
            if reales == esperadas:
                print(f"  {tabla:18} {reales:>6} filas  ok")
            else:
                print(f"  {tabla:18} {reales:>6} != {esperadas}  DESCUADRE")

    total = sum(n for _, n in resumen)
    print(f"\nTotal: {total} filas en {len(resumen)} tablas")

    rotas = contar_huerfanos(origen)
    if rotas:
        print(f"\nReferencias rotas en la base local: {sum(n for _, n in rotas)} filas")
        for referencia, n in rotas:
            print(f"  {referencia:42} {n:>6}")
        print("Se copian igual (asi estan en la base local). Las claves foraneas de\n"
              "Neon quedan como NOT VALID para que el historico roto no las rechace.")

    if args.dry_run:
        print("\nSimulacion terminada. Sin cambios. Corre sin --dry-run para aplicar.")
    else:
        print("\nListo. Neon ya tiene los datos de la base local.")
    print("Para que la app use Neon, poné LOCAL_DATABASE_URL en el .env.")

    if problemas:
        print(f"\nAVISO: {len(problemas)} filas tuvieron valores que no se pudieron"
              "\nconvertir y quedaron en NULL. Revisá:")
        for tabla, avisos in problemas[:10]:
            print(f"  {tabla}: {avisos[:3]}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
