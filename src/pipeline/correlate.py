"""Etapa 6 — Correlación noticias ↔ mercado (Gold).

JOIN temporal entre gold_enriched_news y gold_market_prices resolviendo el
siguiente día hábil con el calendario XMEX. Dos modalidades: directo (ticker que
cotiza) y proxy (fintech sin cotización → sector → ticker BMV, is_proxy = true).

STUB DEL SCAFFOLD — implementación guiada por el skill `medallion-pipeline`.
"""

import sys


def main() -> int:
    print(
        "[correlate] etapa no implementada — este es el andamiaje.\n"
        "  Guía: .claude/skills/medallion-pipeline/SKILL.md",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
