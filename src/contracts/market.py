"""Contratos de datos estructurados: `MarketPrice` y `MacroIndicator`.

A diferencia de las noticias, estas fuentes llegan ya tabuladas (filas ×
columnas), así que su contrato es **de tipos y rangos, no semántico** (PRD §4.4
paso 2). No hay regla de "al menos un ticker": el ticker es la clave.

Lo que sí se valida y el PRD no menciona explícitamente: la **coherencia OHLC**.
Un registro con `low > high` pasa cualquier chequeo de "precio > 0" y aun así es
basura que corrompería los retornos calculados en Gold.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.contracts.rejections import DeadLetter, RejectionReason


class MarketPrice(BaseModel):
    """Fila de `silver_market_prices`. Única por `(ticker, date)`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticker: str = Field(min_length=1, max_length=32)
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    adj_close: float = Field(gt=0)
    volume: int = Field(ge=0)
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_batch_uuid: UUID

    @field_validator("ticker")
    @classmethod
    def _normalizar_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _coherencia_ohlc(self) -> MarketPrice:
        if self.low > self.high:
            raise ValueError(f"low ({self.low}) > high ({self.high})")
        # open y close deben caer dentro del rango del día. Yahoo Finance a
        # veces devuelve filas incoherentes en sesiones con ajustes corporativos.
        for nombre, valor in (("open", self.open), ("close", self.close)):
            if not (self.low <= valor <= self.high):
                raise ValueError(
                    f"{nombre} ({valor}) fuera del rango low-high "
                    f"({self.low}-{self.high})"
                )
        return self


class MacroIndicator(BaseModel):
    """Fila de `silver_macro_indicators`. Única por `(series_id, date)`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    series_id: str = Field(min_length=1, max_length=32)
    date: date
    # Sin restricción de signo: hay series legítimamente negativas (variaciones
    # intermensuales del INPC, por ejemplo). El rango se valida por serie en
    # Gold, no aquí.
    value: float
    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_batch_uuid: UUID

    @field_validator("series_id")
    @classmethod
    def _normalizar_serie(cls, v: str) -> str:
        return v.strip().upper()


def validar_precio(crudo: dict[str, Any], batch_uuid: UUID) -> MarketPrice | DeadLetter:
    """Contrato de precios OHLCV. Devuelve el modelo o el rechazo tipado."""
    try:
        return MarketPrice(raw_batch_uuid=batch_uuid, **crudo)
    except ValidationError as exc:
        return DeadLetter(
            guid=None,
            source="yahoo_finance",
            raw_payload=_serializable(crudo),
            rejection_reason=_motivo_estructurado(exc),
            rejection_detail=_resumir(exc),
            batch_uuid=batch_uuid,
        )


def validar_macro(crudo: dict[str, Any], batch_uuid: UUID) -> MacroIndicator | DeadLetter:
    """Contrato de series del SIE. Devuelve el modelo o el rechazo tipado."""
    try:
        return MacroIndicator(raw_batch_uuid=batch_uuid, **crudo)
    except ValidationError as exc:
        return DeadLetter(
            guid=None,
            source="banxico",
            raw_payload=_serializable(crudo),
            rejection_reason=_motivo_estructurado(exc),
            rejection_detail=_resumir(exc),
            batch_uuid=batch_uuid,
        )


# --- Auxiliares ------------------------------------------------------------


def _motivo_estructurado(exc: ValidationError) -> RejectionReason:
    for error in exc.errors():
        tipo = str(error.get("type", ""))
        if error.get("type") == "missing":
            return RejectionReason.MISSING_FIELD
        # greater_than, greater_than_equal y las incoherencias OHLC (que llegan
        # como value_error desde el model_validator) son todas de rango.
        if tipo.startswith("greater_than") or tipo.startswith("less_than"):
            return RejectionReason.OUT_OF_RANGE
        if tipo == "value_error":
            return RejectionReason.OUT_OF_RANGE
    return RejectionReason.TYPE_MISMATCH


def _resumir(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
        for e in exc.errors()
    )[:2048]


def _serializable(crudo: dict[str, Any]) -> dict[str, Any]:
    """JSONB no admite date/datetime nativos; se pasan a ISO 8601."""
    return {
        k: (v.isoformat() if isinstance(v, date | datetime) else v)
        for k, v in crudo.items()
    }
