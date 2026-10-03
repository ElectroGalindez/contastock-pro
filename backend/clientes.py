from typing import Dict, Any, Optional
from sqlalchemy import text
from .db import engine
from .logs import registrar_log

def get_client(cliente_id: str) -> Optional[Dict[str, Any]]:
    """Obtiene un cliente por ID"""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT id, nombre, telefono, ci, chapa, direccion, deuda_total, es_gestor FROM clientes WHERE id = :id"), {"id": cliente_id})
        row = result.first()
        return dict(row._mapping) if row else None



def add_client(nombre, telefono, ci, direccion, chapa, usuario=None):
    """
    Agrega un nuevo cliente a la base de datos.
    Si se pasa 'usuario', se registra quién realizó la acción en la auditoría.
    """
    if not (nombre or "").strip():
        raise ValueError("El nombre del cliente es obligatorio.")
    try:
        with engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO clientes (nombre, telefono, ci, direccion, chapa)
                    VALUES (:nombre, :telefono, :ci, :direccion, :chapa)
                """),
                {
                    "nombre": nombre.strip(),
                    "telefono": telefono.strip() if telefono else None,
                    "ci": ci.strip() if ci else None,
                    "direccion": direccion.strip() if direccion else None,
                    "chapa": chapa.strip() if chapa else None
                }
            )

                # Validar tipo de usuario antes de registrar el log
        if usuario and isinstance(usuario, (str, int)):
            registrar_log(
                usuario=str(usuario),
                accion="crear_cliente",
                detalles={
                    "nombre": nombre,
                    "telefono": telefono,
                    "ci": ci,
                    "direccion": direccion,
                    "chapa": chapa
                }
            )

        # ✅ Obtener el cliente recién insertado
        with engine.connect() as conn:
            nuevo = conn.execute(
                text("SELECT * FROM clientes ORDER BY id DESC LIMIT 1")
            ).first()
            return dict(nuevo._mapping) if nuevo else None

    except Exception as e:
        print(f"❌ Error al crear cliente: {e}")
        raise ValueError("Error al registrar el cliente. Verifica los datos e inténtalo nuevamente.")

def update_client(cliente_id: str, nombre=None, telefono=None, ci=None, chapa=None, direccion=None, usuario=None):
    """
    Actualiza los datos de un cliente existente en la base de datos.
    Parámetros opcionales: nombre, telefono, ci, chapa, direccion
    """
    with engine.begin() as conn:
        cliente = get_client(cliente_id)
        if not cliente:
            raise ValueError(f"No existe el cliente con ID {cliente_id}")

        # Preparar los valores nuevos, manteniendo los anteriores si no se pasan
        nombre = nombre if nombre is not None else cliente.get("nombre")
        if not (nombre or "").strip():
            raise ValueError("El nombre del cliente es obligatorio.")
        telefono = telefono or cliente.get("telefono", "")
        ci = ci or cliente.get("ci", "")
        chapa = chapa or cliente.get("chapa", "")
        direccion = direccion or cliente.get("direccion", "")

        query = text("""
            UPDATE clientes
            SET nombre = :nombre,
                telefono = :telefono,
                ci = :ci,
                chapa = :chapa,
                direccion = :direccion
            WHERE id = :id
        """)
        conn.execute(query, {
            "id": cliente_id,
            "nombre": nombre,
            "telefono": telefono,
            "ci": ci,
            "chapa": chapa,
            "direccion": direccion
        })

    registrar_log(usuario or "sistema", "update_client", {"id": cliente_id})
    return get_client(cliente_id)

def delete_client(cliente_id: str, usuario: str = "sistema") -> bool:
    """Elimina un cliente"""
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM clientes WHERE id = :id"), {"id": cliente_id})
        registrar_log(usuario, "delete_client", {"id": cliente_id})
        return True
    except Exception as e:
        registrar_log(usuario, "error_delete_client", {"id": cliente_id, "error": str(e)})
        raise

def update_debt(cliente_id: str, monto: float, usuario: str = "sistema") -> Dict[str, Any]:
    """Actualiza la deuda del cliente de manera segura"""
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                UPDATE clientes
                SET deuda_total = CASE WHEN deuda_total + :monto < 0 THEN 0
                                       ELSE deuda_total + :monto END
                WHERE id = :id
            """), {"id": cliente_id, "monto": monto})
        registrar_log(usuario, "update_debt", {"id": cliente_id, "monto": monto})
        return get_client(cliente_id)
    except Exception as e:
        registrar_log(usuario, "error_update_debt", {"id": cliente_id, "monto": monto, "error": str(e)})
        raise


def recalcular_deuda_cliente(cliente_id: str, usuario: str = "sistema") -> float:
    """Recalcula deuda_total del cliente a partir de lo que REALMENTE debe:
    suma de los montos restantes (cantidad * precio) de sus deudas pendientes.
    Se usa al pagar deudas para mantener el saldo sincronizado."""
    try:
        with engine.begin() as conn:
            calculada = conn.execute(text("""
                SELECT COALESCE(SUM(dd.cantidad * dd.precio_unitario), 0)
                FROM deudas_detalle dd
                JOIN deudas d ON d.id = dd.deuda_id
                WHERE d.cliente_id = :id AND d.estado = 'pendiente'
                                  AND dd.estado = 'pendiente'
            """), {"id": cliente_id}).scalar() or 0

            # Las cantidades se restan por división, así que la suma puede dar
            # 399.99999999999994 en vez de 400. Redondeado a centavos, que es
            # como se maneja el dinero en el resto del módulo.
            monto = round(float(calculada), 2)

            conn.execute(text("""
                UPDATE clientes
                SET deuda_total = :monto
                WHERE id = :id
            """), {"id": cliente_id, "monto": monto})
        if usuario:
            registrar_log(usuario, "recalcular_deuda", {"id": cliente_id, "monto": monto})
        return monto
    except Exception as e:
        if usuario:
            registrar_log(usuario, "error_recalcular_deuda", {"id": cliente_id, "error": str(e)})
        raise

def list_clients(limit=None, offset=None) -> list[Dict[str, Any]]:
    """Lista todos los clientes con campos esenciales"""
    sql = "SELECT id, nombre, telefono, ci, chapa, direccion, deuda_total, es_gestor FROM clientes ORDER BY nombre"
    params = {}
    if limit is not None:
        sql += " LIMIT :limit"
        params["limit"] = limit
    if offset is not None:
        sql += " OFFSET :offset"
        params["offset"] = offset
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return [dict(r._mapping) for r in result]


def listar_gestores() -> list[Dict[str, Any]]:
    """Clientes marcados como gestores, para el selector de /ventas."""
    with engine.connect() as conn:
        filas = conn.execute(text(
            "SELECT id, nombre, telefono, ci, chapa FROM clientes "
            "WHERE es_gestor = 1 ORDER BY nombre"
        )).mappings().all()
    return [dict(r) for r in filas]


def count_gestores() -> int:
    with engine.connect() as conn:
        return conn.execute(text(
            "SELECT COUNT(*) FROM clientes WHERE es_gestor = 1")).scalar()


def _a_bool(valor) -> bool:
    if isinstance(valor, bool):
        return valor
    return str(valor).strip().lower() in ("1", "true", "t", "yes", "si", "sí", "on")


def set_gestor(cliente_id, es_gestor, usuario: str = "sistema") -> Optional[Dict[str, Any]]:
    """Marca o desmarca un cliente como gestor. Sigue siendo un cliente normal.

    No crea una tabla ni una entidad nueva: es solo una bandera, asi que el
    gestor puede comprar, deber, aparecer en /clientes y en las ventas igual que
    cualquier otro cliente.
    """
    valor = 1 if _a_bool(es_gestor) else 0
    with engine.begin() as conn:
        conn.execute(text("UPDATE clientes SET es_gestor = :v WHERE id = :i"),
                     {"v": valor, "i": cliente_id})
    if usuario:
        registrar_log(usuario, "marcar_gestor" if valor else "desmarcar_gestor",
                      {"cliente_id": cliente_id})
    return get_client(cliente_id)


def set_gestores_masivo(cliente_ids, es_gestor, usuario: str = "sistema") -> int:
    """Marca o desmarca varios clientes de una vez. Devuelve cuántos cambió."""
    # Un id suelto llega como "12" y un for sobre eso lo recorre caracter por
    # caracter, marcando clientes equivocados. Se normaliza a lista de enteros
    # y se descarta lo que no sea un id valido.
    if cliente_ids is None:
        ids = []
    elif isinstance(cliente_ids, (str, bytes, int)):
        ids = [cliente_ids]
    else:
        ids = list(cliente_ids)

    limpio = []
    for i in ids:
        try:
            limpio.append(int(str(i).strip()))
        except (TypeError, ValueError):
            continue
    if not limpio:
        return 0

    valor = 1 if _a_bool(es_gestor) else 0
    lista_ids = ",".join(str(i) for i in limpio)
    with engine.begin() as conn:
        res = conn.execute(text(
            f"UPDATE clientes SET es_gestor = :v WHERE id IN ({lista_ids})"),
            {"v": valor})
    total = res.rowcount if res.rowcount and res.rowcount > 0 else len(limpio)
    if usuario:
        registrar_log(usuario, "gestores_masivo" if valor else "quitar_gestores_masivo",
                      {"clientes": len(limpio), "total": total})
    return total

def count_clients() -> int:
    with engine.connect() as conn:
        return conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar()


def _normaliza(texto) -> str:
    """Minúsculas y sin acentos, igual que el normalizar() del selector de /ventas.

    Si estas dos reglas difieren, un cliente puede aparecer en un módulo y no
    en el otro, que es justo lo que reportaba el usuario.
    """
    import unicodedata
    s = "" if texto is None else str(texto)
    s = s.lower().strip()
    s = unicodedata.normalize("NFD", s)
    return "".join(c for c in s if unicodedata.category(c) != "Mn")


_CAMPOS_BUSQUEDA = ("id", "nombre", "telefono", "ci", "chapa", "direccion")


def search_clients(q: str, limit: int = None, offset: int = 0) -> list[Dict[str, Any]]:
    """Busca clientes por nombre, ID, teléfono, CI, chapa o dirección, en TODOS.

    La coincidencia es sin acentos y sin distinguir mayúsculas, y todas las
    palabras deben aparecer, con el mismo criterio que el filtro del selector
    de /ventas: si difieren, un cliente aparece en un módulo y no en el otro.

    El filtrado se hace en Python a propósito. Un pre-filtro tipo
    ``nombre LIKE '%rodriganez%'`` es sensible a acentos y descartaría
    "Rodrigáñez" justamente cuando el usuario escribe sin tilde, que es el caso
    más común en Paraguay. Con ~2000 clientes el escaneo es barato, y además
    el buscador ya va con debounce de 220 ms.

    El orden prioriza el ID exacto. Sin esto, buscar "2" coincide con ~1400
    clientes y el que se busca puede quedar fuera de los primeros 100, que es
    la misma confusión que reportó el usuario al buscar por ID.
    """
    palabras = [_normaliza(p) for p in (q or "").split() if _normaliza(p)]
    if not palabras:
        return []

    qnorm = _normaliza(q)
    es_numero = qnorm.isdigit()

    with engine.connect() as conn:
        filas = conn.execute(text(
            "SELECT " + ", ".join(_CAMPOS_BUSQUEDA) + ", deuda_total, es_gestor FROM clientes"
        )).mappings().all()

    out = []
    for c in filas:
        heno = _normaliza(" ".join(
            str(c[k]) if c[k] is not None else "" for k in _CAMPOS_BUSQUEDA
        ))
        if not all(p in heno for p in palabras):
            continue
        c = dict(c)
        if es_numero and str(c.get("id")) == qnorm:
            c["_rango"] = 0                      # ID exacto: siempre primero
        elif any(_normaliza(c[k]) == qnorm for k in _CAMPOS_BUSQUEDA if c[k]):
            c["_rango"] = 1                      # otro campo con valor exacto
        elif any(_normaliza(c[k]).startswith(qnorm)
                 for k in _CAMPOS_BUSQUEDA if c[k]):
            c["_rango"] = 2                      # empieza por lo buscado
        else:
            c["_rango"] = 3                      # solo contiene
        out.append(c)

    out.sort(key=lambda c: (c.pop("_rango"), _normaliza(c.get("nombre"))))
    if offset:
        out = out[offset:]
    if limit is not None:
        out = out[:limit]
    return out


def count_clients_search(q: str) -> int:
    return len(search_clients(q))


def clientes_para_selector() -> list[Dict[str, Any]]:
    """Datos de todos los clientes para el selector con filtro en el navegador.

    Usa claves cortas porque el JSON viaja embebido en el HTML del registro de
    ventas y esa lista puede tener miles de clientes.

    'deb' indica si el cliente tiene deudas PENDIENTES de verdad, preguntando a
    la tabla deudas. No se usa clientes.deuda_total porque esa columna quedó
    desactualizada en muchos clientes y terminaba marcando como deudor a gente
    que ya no debe nada."""
    sql = """
        SELECT c.id, c.nombre, c.telefono, c.ci, c.chapa, c.es_gestor,
               COALESCE((SELECT SUM(d.monto_total) FROM deudas d
                         WHERE d.cliente_id = c.id AND d.estado <> 'pagada'), 0) AS deuda_real,
               (SELECT COUNT(*) FROM deudas d
                WHERE d.cliente_id = c.id AND d.estado <> 'pagada') AS deudas_pendientes
        FROM clientes c
        ORDER BY c.nombre
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    return [{"i": r["id"], "n": r["nombre"] or "", "t": r["telefono"] or "",
             "c": r["ci"] or "", "h": r["chapa"] or "",
             "d": float(r["deuda_real"] or 0), "deb": bool(r["deudas_pendientes"]),
             "g": bool(r["es_gestor"])}
            for r in rows]


def edit_client(cliente_id: str, nombre: Optional[str] = None, telefono: Optional[str] = None,
                ci: Optional[str] = None, chapa: Optional[str] = None, direccion: Optional[str] = None,
                usuario: str = "sistema") -> Dict[str, Any]:
    """Edita un cliente existente"""
    cambios = {}
    if nombre is not None:
        cambios["nombre"] = nombre
    if telefono is not None:
        cambios["telefono"] = telefono
    if ci is not None:
        cambios["ci"] = ci
    if chapa is not None:
        cambios["chapa"] = chapa
    if direccion is not None:
        cambios["direccion"] = direccion
    return update_client(cliente_id, cambios, usuario)