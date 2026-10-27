"""
Tests de la subida automática a Neon (backend/sync_neon.py) y del modo
consulta. La subida real a Postgres no se puede probar acá (los tests corren
sobre SQLite); se prueba lo que decide cuándo subir, que la base local nunca
se toque y que una PC con otra base no pise a Neon.
"""
from datetime import datetime

import pytest
from sqlalchemy import text

from app import create_app
from backend import config, db, sync_neon
from backend.clientes import add_client


# ------------------------------------------------------
# Cuándo toca subir
# ------------------------------------------------------
def test_horas_config_ignora_lo_que_no_entiende():
    assert sync_neon.horas_config("13:00, 19:30,25:00,hola,7:5") == [(13, 0), (19, 30), (7, 5)]
    assert sync_neon.horas_config("") == []


def test_toca_al_arrancar():
    assert sync_neon.toca_ahora(datetime(2026, 1, 1, 9), None, 10, [], False)


def test_toca_por_intervalo():
    ultima = datetime(2026, 1, 1, 9, 0)
    assert not sync_neon.toca_ahora(datetime(2026, 1, 1, 9, 9), ultima, 10, [], False)
    assert sync_neon.toca_ahora(datetime(2026, 1, 1, 9, 10), ultima, 10, [], False)


def test_toca_a_hora_fija_una_sola_vez():
    ultima = datetime(2026, 1, 1, 12, 59)
    assert sync_neon.toca_ahora(datetime(2026, 1, 1, 13, 0, 20), ultima, 0, [(13, 0)], False)
    # ya subió a las 13:00: no repite hasta el otro día
    ultima = datetime(2026, 1, 1, 13, 0, 20)
    assert not sync_neon.toca_ahora(datetime(2026, 1, 1, 13, 30), ultima, 0, [(13, 0)], False)


def test_reintenta_pronto_si_fallo():
    ultima = datetime(2026, 1, 1, 13, 0)
    ahora = datetime(2026, 1, 1, 13, sync_neon.REINTENTO_MIN)
    assert sync_neon.toca_ahora(ahora, ultima, 0, [(13, 0)], True)
    assert not sync_neon.toca_ahora(ahora, ultima, 0, [(13, 0)], False)


# ------------------------------------------------------
# Protección contra pisar Neon con otra base
# ------------------------------------------------------
def test_no_pisa_neon_con_base_vacia():
    assert sync_neon._parece_otra_base({"ventas": 0}, {"ventas": 500, "clientes": 80})


def test_no_pisa_neon_con_base_mucho_mas_chica():
    assert sync_neon._parece_otra_base({"ventas": 10, "clientes": 5},
                                       {"ventas": 400, "clientes": 100})


def test_sube_normal_y_a_neon_vacio():
    assert sync_neon._parece_otra_base({"ventas": 401, "clientes": 100},
                                       {"ventas": 400, "clientes": 100}) is None
    # borrar una venta en el almacén no bloquea la subida
    assert sync_neon._parece_otra_base({"ventas": 399, "clientes": 100},
                                       {"ventas": 400, "clientes": 100}) is None
    assert sync_neon._parece_otra_base({"ventas": 3}, {}) is None


# ------------------------------------------------------
# Foto de la base local
# ------------------------------------------------------
def test_huella_cambia_solo_si_cambian_los_datos(tmp_path):
    add_client(nombre="Ana", telefono="1", ci=None, direccion=None, chapa=None)

    def firma():
        foto = tmp_path / f"foto{datetime.now().timestamp()}.db"
        sync_neon.tomar_foto(config.get_database_path(), foto)
        return sync_neon.huella(sync_neon.leer_foto(foto))

    primera = firma()
    assert firma() == primera
    add_client(nombre="Beto", telefono="2", ci=None, direccion=None, chapa=None)
    assert firma() != primera


def test_sin_internet_no_toca_la_base_local(monkeypatch):
    add_client(nombre="Ana", telefono="1", ci=None, direccion=None, chapa=None)
    monkeypatch.setattr(sync_neon, "_guardar_estado", lambda **e: e)
    with db.engine.connect() as conn:
        antes = conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar()

    # puerto 1 en localhost: conexión rechazada al instante
    estado = sync_neon.sincronizar("postgresql+psycopg2://u:p@127.0.0.1:1/x", forzar=True)

    assert estado["resultado"] == "error"
    assert estado["error"] == "sin conexión a internet"
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar() == antes


def test_sin_url_no_hace_nada(monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    assert sync_neon.sincronizar()["resultado"] == "no_configurada"
    assert sync_neon.iniciar_automatica() is False


# ------------------------------------------------------
# Modo consulta (la PC del jefe mirando Neon)
# ------------------------------------------------------
@pytest.fixture
def consulta(monkeypatch):
    monkeypatch.setenv("CONTASTOCK_SOLO_LECTURA", "1")
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "jefe", "rol": "admin"}
        yield c


def test_consulta_deja_ver(consulta):
    r = consulta.get("/dashboard")
    assert r.status_code == 200
    assert "Modo consulta" in r.get_data(as_text=True)


def test_consulta_bloquea_cambios(consulta):
    r = consulta.post("/clientes", data={"action": "crear", "nombre": "X"},
                      headers={"X-Requested-With": "fetch"})
    assert r.status_code == 403
    assert consulta.post("/inventario", data={"action": "crear", "nombre": "P",
                                              "precio": 1, "cantidad": 1}).status_code == 302
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM clientes")).scalar() == 0
        assert conn.execute(text("SELECT COUNT(*) FROM productos")).scalar() == 0


def test_consulta_deja_iniciar_sesion(consulta):
    r = consulta.post("/login", data={"username": "nadie", "password": "x"})
    assert r.status_code == 200  # muestra el error de login, no el de consulta


def test_sin_consulta_se_puede_guardar(monkeypatch):
    monkeypatch.delenv("CONTASTOCK_SOLO_LECTURA", raising=False)
    app = create_app()
    assert app.config["SOLO_LECTURA"] is False


# ------------------------------------------------------
# Subir solo lo que cambió (ahorra cómputo de Neon)
# ------------------------------------------------------
def _datos(*filas):
    return {"clientes": (["id", "nombre"], list(filas))}


def test_diferencias_detecta_nuevas_cambiadas_y_borradas():
    antes = sync_neon.firmas(_datos((1, "Ana"), (2, "Beto"), (3, "Caro")))
    ahora = sync_neon.firmas(_datos((1, "Ana"), (2, "Beto Editado"), (4, "Dani")))
    subir, borrar = sync_neon.diferencias(antes, ahora)["clientes"]
    assert sorted(subir) == ["2", "4"]
    assert borrar == ["3"]


def test_sin_cambios_no_hay_diferencias():
    f = sync_neon.firmas(_datos((1, "Ana"), (2, "Beto")))
    assert sync_neon.diferencias(f, f) == {}


def test_columna_nueva_vuelve_a_subir_todo():
    antes = sync_neon.firmas(_datos((1, "Ana")))
    ahora = sync_neon.firmas({"clientes": (["id", "nombre", "es_gestor"], [(1, "Ana", 0)])})
    assert sync_neon.diferencias(antes, ahora)["clientes"][0] == ["1"]


def test_sin_cambios_no_se_conecta_a_neon(monkeypatch, tmp_path):
    """Lo más importante para el plan gratis: si nada cambió, Neon ni se entera."""
    add_client(nombre="Ana", telefono="1", ci=None, direccion=None, chapa=None)
    monkeypatch.setattr(sync_neon, "_dir_sync", lambda: tmp_path)
    foto = tmp_path / "f.db"
    sync_neon.tomar_foto(config.get_database_path(), foto)
    sync_neon._guardar_subido(sync_neon.firmas(sync_neon.leer_foto(foto)))
    sync_neon._guardar_estado(ultima_ok=datetime.now().isoformat())

    def no_conectar(*a, **k):
        raise AssertionError("se conectó a Neon sin necesidad")
    monkeypatch.setattr(sync_neon, "_motor_neon", no_conectar)

    assert sync_neon.sincronizar("postgresql+psycopg2://u:p@x/y")["resultado"] == "sin_cambios"


def test_la_url_sola_no_activa_la_subida(monkeypatch):
    """La PC de desarrollo tiene NEON_DATABASE_URL para los scripts: no debe subir."""
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+psycopg2://u:p@127.0.0.1:1/x")
    monkeypatch.delenv("NEON_SYNC", raising=False)
    assert sync_neon.configurada() is False
    assert sync_neon.sincronizar()["resultado"] == "no_configurada"
    assert sync_neon.iniciar_automatica() is False


def test_neon_sync_1_activa_la_subida(monkeypatch):
    monkeypatch.setenv("NEON_DATABASE_URL", "postgresql+psycopg2://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("NEON_SYNC", "1")
    assert sync_neon.configurada() is True


# ------------------------------------------------------
# Subir en cuanto hay cambios
# ------------------------------------------------------
def test_guardar_algo_avisa_para_subir(monkeypatch):
    avisos = []
    monkeypatch.setattr(sync_neon, "avisar_cambio", lambda: avisos.append(1))
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as c:
        with c.session_transaction() as s:
            s["usuario"] = {"id": 1, "username": "admin", "rol": "admin"}
        c.get("/dashboard")
        assert avisos == []            # mirar no sube nada
        c.post("/clientes", data={"action": "crear", "nombre": "Nuevo"})
        assert avisos == [1]           # guardar sí


def test_cambios_seguidos_se_suben_juntos(monkeypatch):
    """Una venta guarda venta + deuda + log: una sola subida, no tres."""
    import threading
    import time
    monkeypatch.setattr(sync_neon, "ESPERA_AGRUPAR", 0.2)
    monkeypatch.setattr(sync_neon, "ESPERA_MAX", 5)
    sync_neon._cambio.clear()

    def avisos():
        for _ in range(3):
            time.sleep(0.05)
            sync_neon._cambio.set()
    threading.Thread(target=avisos).start()
    inicio = time.monotonic()
    sync_neon._esperar_que_se_calme()
    # esperó a que dejaran de llegar avisos, pero no de más
    assert 0.3 < time.monotonic() - inicio < 2


def test_aviso_sin_subida_activa_no_hace_nada(monkeypatch):
    monkeypatch.setattr(sync_neon, "_hilo", None)
    sync_neon._cambio.clear()
    sync_neon.avisar_cambio()
    assert not sync_neon._cambio.is_set()
