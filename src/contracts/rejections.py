"""Motivos de rechazo y modelo de la cola de cuarentena (PRD §5.2).

Regla del skill data-contracts: **nunca descartes silenciosamente un registro
inválido**. Todo lo que no pasa el contrato acaba aquí, con su motivo específico
y su payload original intacto, para que sea revisable a mano.

Los motivos son un enum compartido y no strings sueltos: si cada etapa inventara
el suyo, `SELECT rejection_reason, COUNT(*)` dejaría de ser una métrica de
calidad y pasaría a ser un inventario de erratas.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RejectionReason(StrEnum):
    """Motivo por el que un registro no llegó a Silver."""

    # --- Integridad semántica (PRD §6.2) ---
    MISSING_ENTITY = "MISSING_ENTITY"
    """Sin ticker, sector ni entidad, y no aplicó el bypass macroeconómico."""

    # --- Tipado estricto ---
    TYPE_MISMATCH = "TYPE_MISMATCH"
    """Un campo no es del tipo declarado o incumple longitud/rango."""

    INVALID_URL = "INVALID_URL"
    """`url` ausente o no parseable como URL absoluta."""

    INVALID_DATE = "INVALID_DATE"
    """`published_at` ausente o no interpretable como fecha ISO 8601."""

    MISSING_FIELD = "MISSING_FIELD"
    """Falta un campo obligatorio del contrato."""

    # --- Datos de mercado ---
    OUT_OF_RANGE = "OUT_OF_RANGE"
    """Precio ≤ 0, volumen < 0, o incoherencia OHLC (p. ej. low > high)."""

    # --- Carga ---
    DUPLICATE_KEY = "DUPLICATE_KEY"
    """Colisión de clave natural que el UPSERT no pudo resolver."""

    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"
    """`source` fuera del enum declarado en el esquema Bronze."""


# Clave natural del registro rechazado, por fuente. Es lo que permite que un
# rechazo repetido cuente como `times_rejected` en vez de como fila nueva.
#
# Las noticias ya traían `guid` (SHA-256 de source+url+published_at) y por eso
# deduplicaban desde el 14-ago-2026. Mercado y macro no lo tenían, así que cada
# pasada los reinsertaba: con el lote diario apenas se notaba, pero el refresco
# histórico semanal (26-ago) vuelve a rechazar los MISMOS 'N/E' de Banxico y
# los mismos días malos de Yahoo cada semana. Sin esto son del orden de 120 000
# filas repetidas al año, y una cuarentena así deja de servir como señal de
# salud de fuentes.
#
# Los nombres son los del payload CRUDO de Bronze, no los del contrato: el
# rechazo ocurre precisamente porque el registro no llegó a normalizarse.
_CLAVES_NATURALES: dict[str, tuple[str, ...]] = {
    "yahoo_finance": ("ticker", "date"),
    "yahoo_fundamentals": ("ticker", "period_end"),
    "yahoo_fundamentals_anual": ("ticker", "period_end"),
    "banxico": ("series_id", "fecha"),
    "inegi": ("indicador_id", "periodo"),
}


def guid_natural(source: str, crudo: dict[str, Any]) -> str | None:
    """Clave natural del registro crudo, o None si no se puede componer.

    Devolver None es un resultado legítimo, no un fallo: un registro tan roto
    que le falta su propia clave no tiene con qué agregarse, y sigue entrando
    como fila nueva cada vez. Es preferible a inventar una clave que colapse
    rechazos distintos en uno.
    """
    campos = _CLAVES_NATURALES.get(source)
    if not campos:
        return None
    valores = [crudo.get(campo) for campo in campos]
    if any(valor is None or valor == "" for valor in valores):
        return None
    return ":".join(str(valor) for valor in valores)


class DeadLetter(BaseModel):
    """Fila de `silver_dead_letters`.

    Conserva `raw_payload` completo: sin el registro original, un rechazo es
    una estadística en vez de algo que se pueda diagnosticar y reprocesar.
    """

    model_config = ConfigDict(extra="forbid")

    guid: str | None = None
    """Puede ser None: si el registro venía tan roto que no se pudo calcular."""

    source: str
    raw_payload: dict[str, Any]
    rejection_reason: RejectionReason
    rejection_detail: str | None = Field(default=None, max_length=2048)
    """Texto libre con el error concreto de Pydantic. Complementa, no sustituye,
    al motivo tipado."""

    batch_uuid: UUID
    rejected_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
