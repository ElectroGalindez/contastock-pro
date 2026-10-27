"""
Tests de la URL de Neon cifrada en el repo (.env.cifrado): sin la clave no se
lee nada, y lo que ya está definido en el entorno se respeta.
"""
import pytest

from backend import secretos

URL = "postgresql://usuario:clave-secreta@ep-ejemplo.neon.tech/neondb"


@pytest.fixture
def archivos(tmp_path, monkeypatch):
    monkeypatch.setattr(secretos, "ARCHIVO", tmp_path / ".env.cifrado")
    monkeypatch.setattr(secretos, "ARCHIVO_CLAVE", tmp_path / ".env.clave")
    monkeypatch.delenv("CONTASTOCK_CLAVE", raising=False)
    return tmp_path


def test_cifra_y_descifra(archivos, monkeypatch):
    assert secretos.cifrar({"NEON_DATABASE_URL": URL}) is True  # creó la clave
    assert b"clave-secreta" not in (archivos / ".env.cifrado").read_bytes()
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    assert secretos.cargar() == ["NEON_DATABASE_URL"]
    import os
    assert os.environ["NEON_DATABASE_URL"] == URL


def test_sin_clave_no_lee_nada(archivos):
    secretos.cifrar({"NEON_DATABASE_URL": URL})
    (archivos / ".env.clave").unlink()
    assert secretos.leer() == {}


def test_clave_equivocada_no_rompe_la_app(archivos):
    secretos.cifrar({"NEON_DATABASE_URL": URL})
    from cryptography.fernet import Fernet
    (archivos / ".env.clave").write_text(Fernet.generate_key().decode())
    assert secretos.leer() == {}


def test_respeta_lo_que_ya_esta_definido(archivos, monkeypatch):
    secretos.cifrar({"NEON_DATABASE_URL": URL})
    monkeypatch.setenv("NEON_DATABASE_URL", "")  # p. ej. los tests la vacían
    assert secretos.cargar() == []


def test_neon_sync_no_viaja_en_el_archivo_compartido(archivos, monkeypatch):
    """Si NEON_SYNC=1 viajara por GitHub, la Mac de desarrollo también subiría."""
    secretos.cifrar({"NEON_DATABASE_URL": URL, "NEON_SYNC": "1"})
    assert "NEON_SYNC" not in secretos.leer()
