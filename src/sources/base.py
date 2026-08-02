"""Contrato común de los adaptadores de fuente.

Regla del PRD §4.4: la ingesta es **fail-soft por fuente**. Ningún adaptador
puede tumbar el batch. Por eso todos devuelven `ResultadoFuente` en vez de
lanzar: un fallo es un dato del lote, no una interrupción.

Y regla del PRD §6.1: los adaptadores devuelven el payload **tal cual**. Su
único trabajo es traer bytes y convertirlos a dicts; nada de limpieza,
normalización ni filtrado. Eso ocurre en Silver.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResultadoFuente:
    """Lo que devuelve un adaptador: o registros, o el motivo de que no haya."""

    source: str
    categoria: str
    registros: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def fallo(cls, source: str, categoria: str, exc: BaseException) -> ResultadoFuente:
        # El tipo de excepción se conserva en el mensaje: distinguir un 403 de
        # un timeout es la diferencia entre "nos bloquean" y "la red falló".
        return cls(
            source=source,
            categoria=categoria,
            error=f"{type(exc).__name__}: {exc}"[:500],
        )
