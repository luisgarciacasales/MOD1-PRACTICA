"""Contrato de estados financieros trimestrales (ampliación 14-ago-2026).

Mismo espíritu que `MarketPrice`: llega ya tabulado, así que el contrato es de
tipos y coherencia, no semántico. La diferencia es que aquí **todos los campos
financieros son opcionales**: yfinance trae huecos NaN dispersos entre
trimestres y emisoras (confirmado contra GFNORTEO.MX — ni un solo trimestre
trae los ocho campos completos). Exigirlos todos habría mandado el corpus
entero a cuarentena. Lo único que se exige es que AL MENOS UNO traiga valor;
una fila sin ningún dato no aporta nada y el contrato la rechaza como
`MISSING_FIELD`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.contracts.rejections import DeadLetter, RejectionReason


class Fundamental(BaseModel):
    """Fila de `silver_fundamentals`. Única por `(ticker, period_end)`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticker: str = Field(min_length=1, max_length=32)
    period_end: date

    # Resultados
    ingresos_totales: float | None = None
    utilidad_neta: float | None = None
    utilidad_por_accion: float | None = None
    # Balance
    activo_total: float | None = Field(default=None, gt=0)
    pasivo_total: float | None = Field(default=None, ge=0)
    capital_contable: float | None = None
    # Flujo de efectivo
    flujo_operativo: float | None = None
    flujo_libre: float | None = None

    ingested_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    raw_batch_uuid: UUID

    @field_validator("ticker")
    @classmethod
    def _normalizar_ticker(cls, v: str) -> str:
        return v.strip().upper()

    @model_validator(mode="after")
    def _al_menos_un_campo(self) -> Fundamental:
        campos = (
            self.ingresos_totales, self.utilidad_neta, self.utilidad_por_accion,
            self.activo_total, self.pasivo_total, self.capital_contable,
            self.flujo_operativo, self.flujo_libre,
        )
        if all(c is None for c in campos):
            raise ValueError("ningún campo financiero trae valor")
        return self


def validar_fundamental(crudo: dict[str, Any], batch_uuid: UUID) -> Fundamental | DeadLetter:
    """Contrato de estados financieros trimestrales."""
    try:
        return Fundamental(raw_batch_uuid=batch_uuid, **crudo)
    except ValidationError as exc:
        return DeadLetter(
            guid=None,
            source="yahoo_fundamentals",
            raw_payload=_serializable(crudo),
            rejection_reason=_motivo_estructurado(exc),
            rejection_detail=_resumir(exc),
            batch_uuid=batch_uuid,
        )


def _motivo_estructurado(exc: ValidationError) -> RejectionReason:
    for error in exc.errors():
        tipo = str(error.get("type", ""))
        if error.get("type") == "missing":
            return RejectionReason.MISSING_FIELD
        if tipo.startswith("greater_than") or tipo.startswith("less_than"):
            return RejectionReason.OUT_OF_RANGE
        if tipo == "value_error":
            # Cubre tanto "ningún campo trae valor" como rangos incoherentes.
            msg = str(error.get("msg", ""))
            if "ningún campo financiero" in msg:
                return RejectionReason.MISSING_FIELD
            return RejectionReason.OUT_OF_RANGE
    return RejectionReason.TYPE_MISMATCH


def _resumir(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(x) for x in e.get('loc', ()))}: {e.get('msg')}"
        for e in exc.errors()
    )[:2048]


def _serializable(crudo: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (v.isoformat() if isinstance(v, date | datetime) else v)
        for k, v in crudo.items()
    }
