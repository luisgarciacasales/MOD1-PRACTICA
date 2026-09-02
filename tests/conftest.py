"""Infraestructura para las pruebas de integración contra PostgreSQL.

**Por qué existen.** Los tres defectos más caros de finales de agosto de 2026
—el backfill que se revertía solo, la precedencia que destruía campos, y los
lotes de Bronze aplicándose en orden de UUID— vivieron días en producción y se
encontraron por casualidad, mirando datos. Ninguno lo habría cazado la batería
de entonces, porque **era enteramente unitaria**: comprobaba que el SQL
contuviera ciertos textos, no que la base se comportara como se esperaba. Un
test que lee una cadena no sabe qué hace un `ON CONFLICT`.

Cada prueba corre contra una base **de verdad**, creada y destruida por la
sesión de pytest. No se toca `mod1_practica`: se crea `mod1_test` aparte, con el
mismo esquema aplicado desde `sql/`, de modo que lo que se prueba es el esquema
real y no una maqueta que puede divergir.

Si no hay PostgreSQL alcanzable, estas pruebas se saltan en vez de fallar: la
batería unitaria tiene que seguir corriendo en un portátil sin infraestructura.
"""

from __future__ import annotations

from pathlib import Path

import pytest

BASE_PRUEBA = "mod1_test"
DIRECTORIO_SQL = Path("/app/sql")

# Las mismas tres de `docker/postgres/init/001_extensions.sql`. Ese archivo se
# monta solo en el contenedor de PostgreSQL, no en el de la aplicación, así que
# aquí se repiten a mano; son estables y su ausencia rompería el esquema de
# forma evidente en la primera migración.
_EXTENSIONES = ("vector", "pg_trgm", "unaccent")


def _dsn_con_base(dsn: str, base: str) -> str:
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    partes = conninfo_to_dict(dsn)
    partes["dbname"] = base
    return make_conninfo(**partes)


@pytest.fixture(scope="session")
def dsn_prueba() -> str:
    """Crea `mod1_test` con el esquema real y la destruye al terminar.

    Se conecta a la base `postgres` para el CREATE/DROP porque no se puede
    borrar una base a la que estás conectado. `DROP ... IF EXISTS` al principio
    limpia lo que hubiera dejado una sesión interrumpida.
    """
    import psycopg

    from src.config import get_settings

    produccion = get_settings().postgres_dsn
    mantenimiento = _dsn_con_base(produccion, "postgres")

    try:
        conexion = psycopg.connect(mantenimiento, autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as exc:
        pytest.skip(f"sin PostgreSQL alcanzable: {exc}")

    with conexion:
        conexion.execute(f"DROP DATABASE IF EXISTS {BASE_PRUEBA} WITH (FORCE)")
        conexion.execute(f"CREATE DATABASE {BASE_PRUEBA}")

    dsn = _dsn_con_base(produccion, BASE_PRUEBA)
    try:
        with psycopg.connect(dsn, autocommit=True) as cx:
            for ext in _EXTENSIONES:
                cx.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
            for archivo in sorted(DIRECTORIO_SQL.glob("*.sql")):
                cx.execute(archivo.read_text(encoding="utf-8"))
        yield dsn
    finally:
        with psycopg.connect(mantenimiento, autocommit=True) as cx:
            cx.execute(f"DROP DATABASE IF EXISTS {BASE_PRUEBA} WITH (FORCE)")


@pytest.fixture
def cur(dsn_prueba: str):
    """Un cursor por prueba, con **rollback** al terminar.

    Aislar por transacción en vez de recrear la base entre pruebas: crear la
    base y aplicar 24 migraciones tarda segundos, y multiplicarlo por cada test
    haría que la batería dejara de ejecutarse a menudo, que es la forma más
    común de que una batería deje de servir.
    """
    import psycopg

    with psycopg.connect(dsn_prueba) as conexion:
        with conexion.cursor() as cursor:
            yield cursor
        conexion.rollback()
