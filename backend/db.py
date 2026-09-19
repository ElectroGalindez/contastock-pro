import os
from sqlalchemy import create_engine, MetaData, text
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv
from contextlib import contextmanager

# ---------------------------
# Cargar .env
# ---------------------------
dotenv_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
load_dotenv(dotenv_path)

DATABASE_URL = os.getenv("LOCAL_DATABASE_URL") or os.getenv("NEON_DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("No se encontró LOCAL_DATABASE_URL ni NEON_DATABASE_URL en el archivo .env")

# ---------------------------
# Motor y sesión
# ---------------------------
engine = create_engine(DATABASE_URL, echo=False, future=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Objeto MetaData global
metadata = MetaData()

# ---------------------------
# Context manager para conexión
# ---------------------------
@contextmanager
def get_connection():
    """
    Devuelve una sesión de SQLAlchemy (context manager).
    Uso:
       with get_connection() as session:  
           session.execute(...)
    """
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

# ---------------------------
# Función de prueba
# ---------------------------
def test_connection():
    with engine.connect() as conn:
        result = conn.execute(text("SELECT NOW()"))
        print("Conexión exitosa ✅", result.scalar())

if __name__ == "__main__":
    test_connection()
