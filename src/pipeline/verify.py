"""Checks de la Definición de Terminado (PRD §8).

Convierte los criterios de aceptación en asserts ejecutables: 5 fuentes en
Bronze, contrato semántico con dead letters, idempotencia, 0 duplicados, NER +
sentimiento + M&A + proxy, calendario XMEX y consulta semántica FAISS.

STUB — la especificación de cada check vive en el skill `acceptance-verify`.

STUB DEL SCAFFOLD — implementación guiada por el skill `medallion-pipeline`.
"""

import sys


def main() -> int:
    print(
        "[verify] etapa no implementada — este es el andamiaje.\n"
        "  Guía: .claude/skills/medallion-pipeline/SKILL.md",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
