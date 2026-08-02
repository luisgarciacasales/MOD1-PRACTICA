"""Contrato `FintechDictEntry` — diccionario maestro Finnovista (PRD §5.2).

Es un diccionario de referencia con carga única y actualización manual, no una
fuente de flujo. Su papel es doble:

1. Cross-reference en el enriquecimiento: etiquetar noticias que involucran
   competencia Fintech vs. banca tradicional.
2. Origen del **proxy ticker**: si `ticker is None`, la fintech no cotiza en la
   BMV y su impacto se mide sobre la emisora proxy de su sector
   (`src/config/tickers.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FintechDictEntry(BaseModel):
    """Fila de `silver_fintech_dict`."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    legal_name: str = Field(min_length=1, max_length=256)
    """Razón social."""

    commercial_name: str = Field(min_length=1, max_length=128)
    """Nombre comercial: es el que aparece en las noticias ("Nu", "Stori")."""

    ticker: str | None = Field(default=None, max_length=32)
    """None para las que no cotizan — la mayoría, y el motivo de que exista el
    mecanismo de proxy."""

    sector: str = Field(min_length=1, max_length=64)
    """"neobanco", "lending", "payments", "insurtech"…"""

    country: str = Field(default="MX", min_length=2, max_length=2)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("ticker")
    @classmethod
    def _normalizar_ticker(cls, v: str | None) -> str | None:
        if v is None or not v.strip():
            return None
        return v.strip().upper()

    @field_validator("country")
    @classmethod
    def _iso_mayusculas(cls, v: str) -> str:
        return v.strip().upper()

    @property
    def cotiza_en_bmv(self) -> bool:
        """Si es False, las noticias sobre esta fintech necesitan proxy ticker."""
        return self.ticker is not None
