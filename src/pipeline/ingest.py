"""Etapa 1 — Ingesta (Origen → Bronze).

Dispara las 5 fuentes en dos pipelines paralelos (noticias y mercado) y
escribe cada lote en /data/bronze/{tipo}/{source}/{YYYY-MM-DD}/{batch_id}/ como
JSON + Parquet, acompañado de metadata.json con batch_uuid, timestamp,
record_count y checksum SHA-256.

Invariante: NO se aplica ninguna transformación de limpieza (PRD §6.1).
Fail-soft: si una fuente falla, el resto del batch continúa.

STUB DEL SCAFFOLD — implementación guiada por el skill `medallion-pipeline`.
"""

import sys


def main() -> int:
    print(
        "[ingest] etapa no implementada — este es el andamiaje.\n"
        "  Guía: .claude/skills/medallion-pipeline/SKILL.md",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
