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
