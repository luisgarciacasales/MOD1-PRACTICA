"""Diccionario maestro Fintech — Finnovista Radar (PRD §3.3).

A diferencia de las otras cuatro, esta fuente **no se descarga**: el PRD la
define como "carga única con actualización manual bajo demanda". Se versiona
como semilla en `seed/finnovista_radar.json` y se ingiere desde ahí.

Que sea un archivo del repo es deliberado: es dato de referencia, no dato
operativo. Cambia por decisión humana, debe revisarse en un diff y no puede
depender de que un sitio externo siga en pie el día del batch.

Ninguna de las entradas tiene ticker: ese es justamente el motivo de que exista
el mecanismo de proxy ticker (PRD §3.3). Si alguna llegara a listarse en la
BMV, basta con rellenar su `ticker` aquí.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.sources.base import ResultadoFuente

RUTA_SEMILLA = Path("/app/seed/finnovista_radar.json")


def ingerir(*, ruta: Path = RUTA_SEMILLA) -> ResultadoFuente:
    try:
        if not ruta.exists():
            raise FileNotFoundError(f"semilla no encontrada en {ruta}")

        entradas: list[dict[str, Any]] = json.loads(ruta.read_text(encoding="utf-8"))
        if not entradas:
            raise ValueError("la semilla está vacía")

        registros = [{**entrada, "source": "finnovista"} for entrada in entradas]
        return ResultadoFuente(
            source="finnovista", categoria="market", registros=registros
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft es el contrato
        return ResultadoFuente.fallo("finnovista", "market", exc)
