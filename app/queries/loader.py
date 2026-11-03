import functools
from pathlib import Path

# Ruta absoluta hacia la carpeta app/queries/sql/
QUERIES_DIR = Path(__file__).parent / "sql"


@functools.lru_cache(maxsize=64)
def get_query(query_name: str) -> str:
    """Carga y almacena en caché el contenido de un archivo .sql.
    Ejemplo: get_query("hourly_tonnage") busca app/queries/sql/hourly_tonnage.sql
    """
    sql_file = QUERIES_DIR / f"{query_name}.sql"
    if not sql_file.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo de consulta SQL: {sql_file}"
        )
    return sql_file.read_text(encoding="utf-8")