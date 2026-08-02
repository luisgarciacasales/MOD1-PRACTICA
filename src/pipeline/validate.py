"""Etapa 2-3 — Validación y carga idempotente (Bronze → Silver).

Aplica los contratos Pydantic y enruta los rechazos a silver_dead_letters con
su motivo. Los registros válidos entran con INSERT ... ON CONFLICT DO UPDATE
sobre la clave natural, de modo que reprocesar el mismo lote da filas_nuevas = 0.

STUB DEL SCAFFOLD — implementación guiada por el skill `medallion-pipeline`.
"""

import sys


def main() -> int:
    print(
        "[validate] etapa no implementada — este es el andamiaje.\n"
        "  Guía: .claude/skills/medallion-pipeline/SKILL.md",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
