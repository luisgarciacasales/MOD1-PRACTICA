"""Contrato de estados financieros, trimestrales y anuales (ampliación
14-ago-2026, extendida 25-ago-2026).

Mismo espíritu que `MarketPrice`: llega ya tabulado, así que el contrato es de
tipos y coherencia, no semántico. La diferencia es que aquí **todos los campos
financieros son opcionales**: yfinance trae huecos NaN dispersos entre
periodos y emisoras (confirmado contra GFNORTEO.MX — ni un solo trimestre
trae los ocho campos completos). Exigirlos todos habría mandado el corpus
entero a cuarentena. Lo único que se exige es que AL MENOS UNO traiga valor;
una fila sin ningún dato no aporta nada y el contrato la rechaza como
`MISSING_FIELD`.

Un solo modelo sirve para trimestral y anual — la FORMA de la fila (mismos
ocho campos financieros) es idéntica; lo único que cambia es a qué tabla
Silver aterriza, decisión que toma el llamador (`validate.py`), no el
contrato. `validar_fundamental` recibe `source` para que la cuarentena
distinga de qué serie vino cada rechazo, igual que cualquier otra fuente.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from src.contracts.rejections import DeadLetter, RejectionReason


class Fundamental(BaseModel):
    """Fila de `silver_fundamentals` (trimestral) o `silver_fundamentals_anual`
    (anual, ver `validar_fundamental`). Única por `(ticker, period_end)` en
    cada tabla — no hay colisión entre ambas porque viven separadas."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    ticker: str = Field(min_length=1, max_length=32)
    period_end: date

    # Resultados
    # `ge=0`: un ingreso total negativo no existe —son ventas, no un neto— así
    # que su presencia delata un dato derivado, no observado. Yahoo construye
    # el trimestre de las emisoras mexicanas restando periodos, y cuando los
    # operandos no casan devuelve negativos: GFNORTEO 2025-06-30 traía
    # ingresos −13,555 mdp y utilidad −670 mdp, con las seis últimas cifras
    # idénticas a las del trimestre anterior (…361,681 y …111,461) — la firma
    # aritmética de la resta. Si el estado de resultados vino mal derivado, el
    # resto de la fila tampoco está garantizado, así que va entera a cuarentena
    # en vez de contaminar el ROE y el P/U con un trimestre inventado.
    ingresos_totales: float | None = Field(default=None, ge=0)
    utilidad_neta: float | None = None
    utilidad_por_accion: float | None = None
    # Balance
    activo_total: float | None = Field(default=None, gt=0)
    pasivo_total: float | None = Field(default=None, ge=0)
    capital_contable: float | None = None
    # Acciones en circulación (F2, 25-ago-2026): "Ordinary Shares Number" del
    # mismo balance — la pieza que faltaba para P/VL (book_value_per_share =
    # capital_contable / acciones_en_circulacion). No es ingreso/utilidad/
    # activo, pero vive en el mismo estado financiero y comparte exactamente
    # el mismo patrón de huecos dispersos que el resto — un campo más, no una
    # fuente nueva.
    acciones_en_circulacion: float | None = Field(default=None, gt=0)
    # Flujo de efectivo
    flujo_operativo: float | None = None
    flujo_libre: float | None = None

    # Origen del dato, para que la precedencia entre fuentes viva EN LOS DATOS
    # y no en el orden en que se corran los comandos (29-ago-2026). El backfill
    # desde los PDF de resultados era efímero: `validate --todo` revalida todo
    # Bronze y devolvía la fila a los valores de Yahoo, así que cada `verify`
    # —que corre validate --todo por dentro— lo deshacía. Con este campo, el
    # UPSERT de `yahoo` no pisa una fila de `reporte_pdf`; ver
    # sql/022_fundamentales_fuente.sql y _SQL_FUNDAMENTALES en db.py.
    fuente: Literal["yahoo", "reporte_pdf"] = "yahoo"

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
            self.acciones_en_circulacion, self.flujo_operativo, self.flujo_libre,
        )
        if all(c is None for c in campos):
            raise ValueError("ningún campo financiero trae valor")
        return self


def validar_fundamental(
    crudo: dict[str, Any], batch_uuid: UUID, *, source: str = "yahoo_fundamentals"
) -> Fundamental | DeadLetter:
    """Contrato de estados financieros. `source` distingue trimestral
    (`yahoo_fundamentals`) de anual (`yahoo_fundamentals_anual`) solo para
    etiquetar el rechazo — el contrato en sí es idéntico para ambos."""
    try:
        return Fundamental(raw_batch_uuid=batch_uuid, **crudo)
    except ValidationError as exc:
        return DeadLetter(
            guid=None,
            source=source,
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
