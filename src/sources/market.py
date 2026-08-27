"""Precios OHLCV vía yfinance (PRD §3.4).

Los símbolos vienen de `src/config/tickers.py`, ya corregidos: tres de los ocho
del PRD no existen en Yahoo porque les faltaba la serie accionaria (ADR-11).

Se descarga `TICKERS_MERCADO`, que son las emisoras **más el benchmark** `^MXX`.
El índice se ingiere como cualquier serie de precios pero se excluye del
vocabulario del NER y de la correlación: ver la nota de BENCHMARK en la config.

Sobre el caché: yfinance 0.2.66 gestiona su propia sesión HTTP y ya no acepta
de forma fiable una `requests.Session` externa, así que `requests-cache` no
puede interceptarlo aquí. La mitigación del riesgo nº7 del PRD (rate limiting)
se aplica de otro modo: lista de tickers acotada a 8 y una pausa entre
solicitudes. El caché sí opera en BANXICO, que es HTTP plano.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from src.config.tickers import (
    TICKERS_MERCADO,
    VENTANA_PRECIOS_DIARIA,
    ventana_historica_ticker,
)
from src.sources.base import ResultadoFuente

# El PRD §9 recomienda espaciar 0.5–1 s entre solicitudes en producción.
PAUSA_ENTRE_TICKERS = 0.6


def ingerir(
    *,
    tickers: tuple[str, ...] = TICKERS_MERCADO,
    modo: str = "diario",
    periodo: str | None = None,
) -> ResultadoFuente:
    """Descarga precios de cada ticker.

    Dos velocidades (ver el bloque de ventanas en `src/config/tickers.py`):

    · `modo="diario"` — `VENTANA_PRECIOS_DIARIA`. Es lo que corre cada día: solo
      trae lo que puede haber cambiado, que es la vela en curso consolidándose.
    · `modo="completo"` — ventana por ticker (`max` para los de historia
      completa, el tope general para el resto). Es lo que repara el reajuste
      retroactivo de `Adj Close` tras un dividendo o un split, que reescala la
      serie entera. Va semanal porque el universo genera del orden de 25 de esos
      eventos al año: uno cada dos semanas, así que espaciarlo más dejaría la
      serie profunda desajustada casi siempre.

    `periodo` fuerza una ventana concreta e ignora el modo; lo usan los
    backfills puntuales.

    Fail-soft **por ticker además de por fuente**: que Yahoo no reconozca un
    símbolo no puede costar los otros siete. Los fallos individuales se anotan
    en el resultado y solo se declara caída la fuente si no se obtuvo nada.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        return ResultadoFuente.fallo("yahoo_finance", "market", exc)

    if modo not in ("diario", "completo"):
        return ResultadoFuente.fallo(
            "yahoo_finance", "market", ValueError(f"modo desconocido: {modo!r}")
        )

    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, ticker in enumerate(tickers):
        # La ventana se resuelve POR TICKER: en modo completo, GFNORTEO pide
        # `max` y el resto el tope general.
        ventana = periodo or (
            VENTANA_PRECIOS_DIARIA if modo == "diario" else ventana_historica_ticker(ticker)
        )
        try:
            historico = yf.Ticker(ticker).history(
                period=ventana, interval="1d", auto_adjust=False, raise_errors=False
            )
            if historico is None or historico.empty:
                fallidos.append(f"{ticker}: serie vacía")
                continue

            for indice, fila in historico.iterrows():
                registros.append(_fila_a_dict(ticker, indice, fila))
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{ticker}: {type(exc).__name__}")

        if i < len(tickers) - 1:
            time.sleep(PAUSA_ENTRE_TICKERS)

    if not registros:
        return ResultadoFuente(
            source="yahoo_finance",
            categoria="market",
            error=f"ningún ticker devolvió datos ({'; '.join(fallidos)})"[:500],
        )

    resultado = ResultadoFuente(
        source="yahoo_finance", categoria="market", registros=registros
    )
    if fallidos:
        # Éxito parcial: hay datos, pero queda constancia de qué faltó. No se
        # marca como error porque el lote sí es utilizable.
        resultado.registros.append(
            {"_parciales": True, "tickers_fallidos": fallidos, "source": "yahoo_finance"}
        )
    return resultado


def _fila_a_dict(ticker: str, indice: Any, fila: Any) -> dict[str, Any]:
    """Traduce una fila de pandas al esquema que espera el contrato MarketPrice.

    Los nombres se pasan a snake_case aquí porque son nombres de columna de la
    librería, no contenido del dato: Bronze conserva los valores intactos.
    """
    fecha = indice.date() if hasattr(indice, "date") else date.fromisoformat(str(indice)[:10])
    return {
        "ticker": ticker,
        "date": fecha.isoformat(),
        "open": _num(fila.get("Open")),
        "high": _num(fila.get("High")),
        "low": _num(fila.get("Low")),
        "close": _num(fila.get("Close")),
        # Yahoo devuelve 'Adj Close' solo con auto_adjust=False; si faltara,
        # cae a 'Close' para no romper el contrato, que lo exige NOT NULL.
        "adj_close": _num(fila.get("Adj Close", fila.get("Close"))),
        "volume": int(fila.get("Volume") or 0),
        "source": "yahoo_finance",
    }


def _num(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    # NaN se convierte a None para que el contrato lo rechace como campo
    # faltante en vez de propagar un float que rompe las comparaciones.
    return None if numero != numero else numero
