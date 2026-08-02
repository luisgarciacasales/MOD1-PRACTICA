"""Etapa 4 — Enriquecimiento NLP (Silver → Gold).

Toma silver_news con enriched = false y los manda al lab-ollama compartido en
lotes de 8 llamadas concurrentes (asyncio + aiohttp). Extrae NER, sentimiento,
eventos M&A y tagging fintech; infiere el sector afectado para el proxy ticker.

STUB DEL SCAFFOLD — implementación guiada por el skill `medallion-pipeline`.
"""

import sys


def main() -> int:
    print(
        "[enrich] etapa no implementada — este es el andamiaje.\n"
        "  Guía: .claude/skills/medallion-pipeline/SKILL.md",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
