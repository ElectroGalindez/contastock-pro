# backend/usuarios.py
from datetime import datetime, timedelta
import bcrypt
from sqlalchemy import text
from backend.db import engine, ensure_datetime
from .logs import registrar_log

# ============================================
# 🚀 Funciones de Usuarios (Optimizada)
# ============================================

def crear_usuario(username, password, rol="empleado", actor=None):
    """Crea un usuario con password encriptado."""
    hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    query = text("""
        INSERT INTO usuarios (username, password, rol)
        VALUES (:username, :password, :rol)
    """)
    try:
        with engine.begin() as conn:  # begin() → asegura commit/rollback automático
            conn.execute(query, {"username": username, "password": hashed, "rol": rol})
        registrar_log(usuario=actor or username, accion="crear_usuario", detalles={"username": username, "rol": rol})
        return {"username": username, "rol": rol}
    except Exception as e:
        raise ValueError(f"Error al crear usuario ({username}): {e}")

# --------------------------------------------
def autenticar_usuario(username, password, max_intentos=5, bloqueo_min=15):
    """Autentica usuario, maneja intentos fallidos y bloqueo temporal."""
    now = datetime.now()
    q_select = text("""
        SELECT password, activo, intentos_fallidos, bloqueado_hasta, rol 
        FROM usuarios WHERE username=:username
    """)
    q_reset = text("""
        UPDATE usuarios 
        SET intentos_fallidos=0, bloqueado_hasta=NULL 
        WHERE username=:username
    """)
    q_update = text("""
        UPDATE usuarios 
        SET intentos_fallidos=:intentos, bloqueado_hasta=:bloqueado 
        WHERE username=:username
    """)

    with engine.begin() as conn:
        row = conn.execute(q_select, {"username": username}).mappings().first()
        if not row or not row["activo"]:
            return None  # Usuario no existe o está desactivado

        # Si está bloqueado
        if row["bloqueado_hasta"]:
            bloqueado_hasta = ensure_datetime(row["bloqueado_hasta"])
            if bloqueado_hasta > now:
                return {"bloqueado": True, "bloqueado_hasta": bloqueado_hasta.isoformat()}

        # Contraseña correcta
        if bcrypt.checkpw(password.encode(), row["password"].encode()):
            if row["intentos_fallidos"] or row["bloqueado_hasta"]:
                conn.execute(q_reset, {"username": username})
            return {"username": username, "rol": row["rol"]}

        # Contraseña incorrecta → aumentar intentos
        intentos = (row["intentos_fallidos"] or 0) + 1
        bloqueado = None
        if intentos >= max_intentos:
            bloqueado = now + timedelta(minutes=bloqueo_min)
            registrar_log(usuario=username, accion="bloqueo_usuario",
                          detalles={"motivo": "intentos fallidos", "bloqueado_hasta": bloqueado.isoformat()})
        conn.execute(q_update, {"intentos": intentos, "bloqueado": bloqueado, "username": username})
    return None

# --------------------------------------------
def cambiar_password(username, new_password, actor=None):
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET password=:p, requiere_cambio_password=FALSE WHERE username=:u"),
            {"p": hashed, "u": username}
        )
    registrar_log(usuario=actor or username, accion="cambiar_password", detalles={"username": username})
    return True

# --------------------------------------------
def cambiar_rol(username, nuevo_rol, actor=None):
    old_rol = get_rol(username)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET rol=:r WHERE username=:u"),
            {"r": nuevo_rol, "u": username}
        )
    registrar_log(usuario=actor or username, accion="cambiar_rol",
                  detalles={"username": username, "rol_anterior": old_rol, "rol_nuevo": nuevo_rol})
    return True

# --------------------------------------------
def set_estado_usuario(username, activo: bool, actor=None):
    """Activa o desactiva usuario (uso unificado)."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE usuarios SET activo=:a WHERE username=:u"),
            {"a": activo, "u": username}
        )
    accion = "activar_usuario" if activo else "desactivar_usuario"
    registrar_log(usuario=actor or username, accion=accion, detalles={"username": username})
    return True

# --------------------------------------------
def listar_usuarios():
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT username, rol, activo, created_at, requiere_cambio_password
            FROM usuarios ORDER BY created_at DESC
        """)).mappings().all()
    return [
        {
            "username": r["username"],
            "rol": r["rol"],
            "activo": r["activo"],
            "created_at": ensure_datetime(r["created_at"]).isoformat() if r["created_at"] else None,
            "requiere_cambio_password": r["requiere_cambio_password"]
        }
        for r in rows
    ]

# --------------------------------------------
def requiere_cambio_password(username):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT requiere_cambio_password FROM usuarios WHERE username=:u"),
            {"u": username}
        ).scalar()
    return bool(row)

# --------------------------------------------
def get_rol(username):
    with engine.connect() as conn:
        return conn.execute(
            text("SELECT rol FROM usuarios WHERE username=:u"),
            {"u": username}
        ).scalar()

# --------------------------------------------
def obtener_logs_usuario(username):
    from .logs import obtener_logs_usuario as fetch_logs
    return fetch_logs(username)

def activar_usuario(username, actor=None):
    return set_estado_usuario(username, True, actor)

def desactivar_usuario(username, actor=None):
    return set_estado_usuario(username, False, actor)

# --------------------------------------------
def eliminar_usuario(username, actor=None):
    """Elimina un usuario de la base de datos."""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM usuarios WHERE username=:u"),
            {"u": username}
        )
    registrar_log(usuario=actor or username, accion="eliminar_usuario", detalles={"username": username})
    return True 