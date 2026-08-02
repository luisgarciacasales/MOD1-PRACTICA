"""Aplica las migraciones SQL de `sql/` en orden.

Por qué no basta con `docker/postgres/init/`: ese directorio solo se ejecuta la
PRIMERA vez, cuando el volumen está vacío. El volumen `mod1-practica-pgdata` ya
existe, así que cualquier esquema posterior necesita esta ruta.

Idempotente por partida doble: los propios `.sql` usan `IF NOT EXISTS`, y además
se registra lo aplicado en `schema_migrations` para no reejecutar.

    docker compose exec -T app python -m src.pipeline.migrate
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import psycopg

from src.config import get_settings

DIRECTORIO_SQL = Path("/app/sql")

_TABLA_CONTROL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT        PRIMARY KEY,
    checksum    TEXT        NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def main() -> int:
    settings = get_settings()

    archivos = sorted(DIRECTORIO_SQL.glob("*.sql"))
    if not archivos:
        print(f"[migrate] sin archivos .sql en {DIRECTORIO_SQL}", file=sys.stderr)
        return 1

    with psycopg.connect(settings.postgres_dsn, autocommit=False) as conexion:
        conexion.execute(_TABLA_CONTROL)
        aplicadas = {
            fila[0]: fila[1]
            for fila in conexion.execute(
                "SELECT filename, checksum FROM schema_migrations"
            ).fetchall()
        }

        nuevas = 0
        for archivo in archivos:
            sql = archivo.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()[:16]

            if archivo.name in aplicadas:
                if aplicadas[archivo.name] != checksum:
                    # No la reaplicamos por si el cambio no es idempotente:
                    # avisar es más seguro que adivinar.
                    print(
                        f"[migrate] AVISO: {archivo.name} cambió desde que se "
                        f"aplicó (checksum {aplicadas[archivo.name]} → {checksum}). "
                        f"Crea una migración nueva en vez de editar esta.",
                        file=sys.stderr,
                    )
                else:
                    print(f"[migrate] {archivo.name}: ya aplicada")
                continue

            conexion.execute(sql)  # type: ignore[arg-type]
            conexion.execute(
                "INSERT INTO schema_migrations (filename, checksum) VALUES (%s, %s)",
                (archivo.name, checksum),
            )
            print(f"[migrate] {archivo.name}: aplicada")
            nuevas += 1

        conexion.commit()

    print(f"[migrate] listo — {nuevas} migración(es) nueva(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
