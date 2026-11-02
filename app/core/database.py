import os
import logging
from contextlib import contextmanager
import psycopg2
import time
import threading
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
# Semáforo para serializar el acceso a la única conexión física permitida
_db_lock = threading.Lock()

# Configuración leída de variables de entorno o valores por defecto
DB_HOST = os.getenv("DB_HOST", "10.9.201.248")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "RSP")
DB_USER = os.getenv("DB_USER", "fgonzales")
DB_PASS = os.getenv("DB_PASS", "fgonzales26")

try:
    # ThreadedConnectionPool garantiza concurrencia segura para FastAPI/Uvicorn
    db_pool = ThreadedConnectionPool(
        minconn=int(os.getenv("DB_MIN_CONN", "1")),
        maxconn=int(os.getenv("DB_MAX_CONN", "1")),
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        connect_timeout=int(os.getenv("DB_TIMEOUT", "10"))
    )
    logger.info("Pool de conexiones a PostgreSQL (RSP) inicializado exitosamente.")
except Exception as e:
    logger.error(f"Error al inicializar pool de conexiones: {e}")
    db_pool = None


@contextmanager
def get_db_connection(timeout: float = 10.0):
    """
    Context manager que serializa el acceso a la BD cuando maxconn es muy bajo (1 o 2).
    Espera en cola hasta que la conexión quede libre, evitando 'connection pool exhausted'.
    """
    if db_pool is None:
        raise RuntimeError("El pool de base de datos no está inicializado.")

    inicio = time.time()
    adquirido = _db_lock.acquire(timeout=timeout)
    if not adquirido:
        raise TimeoutError("Tiempo de espera agotado esperando conexión a la base de datos.")

    conn = None
    try:
        conn = db_pool.getconn()
        yield conn
    except Exception:
        if conn and not conn.closed:
            conn.rollback()
        raise
    finally:
        if conn is not None and db_pool is not None:
            try:
                db_pool.putconn(conn)
            except Exception:
                pass
        _db_lock.release()


def get_connection():
    """Obtención manual de conexión (requiere release_connection)."""
    if db_pool is None:
        raise RuntimeError("El pool de base de datos no está inicializado.")
    return db_pool.getconn()


def release_connection(conn):
    """Devuelve la conexión manual al pool."""
    if db_pool and conn:
        db_pool.putconn(conn)


def close_pool():
    """Cierra todas las conexiones del pool al detener la aplicación."""
    if db_pool:
        db_pool.closeall()
        logger.info("Pool de conexiones cerrado limpiamente.")








