"""Estados financieros trimestrales vía yfinance (ampliación 14-ago-2026).

Mismo patrón que `market.py`: fail-soft por ticker además de por fuente, y
pausa entre solicitudes para no maltratar el rate limit de Yahoo. La
diferencia es que aquí se piden TRES estados por ticker (resultados, balance,
flujo) en vez de un histórico de precios — de ahí que la pausa se aplique una
vez por ticker, no por estado.
"""

from __future__ import annotations

import time
from typing import Any

from src.config.fundamentales import TICKERS_FUNDAMENTALES, TODOS_LOS_CAMPOS
from src.sources.base import ResultadoFuente

PAUSA_ENTRE_TICKERS = 0.6


def ingerir(*, tickers: tuple[str, ...] = TICKERS_FUNDAMENTALES) -> ResultadoFuente:
    """Descarga los tres estados financieros trimestrales de cada ticker.

    Fail-soft por ticker: que yfinance no tenga estados de una emisora (o solo
    parcialmente) no puede costar las demás. Se declara caída la fuente solo si
    ningún ticker devolvió un solo trimestre utilizable.
    """
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        return ResultadoFuente.fallo("yahoo_fundamentals", "fundamentals", exc)

    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, ticker in enumerate(tickers):
        try:
            t = yf.Ticker(ticker)
            estados = {
                "income": t.quarterly_income_stmt,
                "balance": t.quarterly_balance_sheet,
                "cashflow": t.quarterly_cashflow,
            }
            periodos = sorted({
                col
                for df in estados.values()
                if df is not None and not df.empty
                for col in df.columns
            })
            if not periodos:
                fallidos.append(f"{ticker}: sin estados financieros")
                continue

            for periodo in periodos:
                fila = _fila_de(ticker, periodo, estados)
                if fila is not None:
                    registros.append(fila)
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{ticker}: {type(exc).__name__}")

        if i < len(tickers) - 1:
            time.sleep(PAUSA_ENTRE_TICKERS)

    if not registros:
        return ResultadoFuente(
            source="yahoo_fundamentals",
            categoria="fundamentals",
            error=f"ningún ticker devolvió estados financieros ({'; '.join(fallidos)})"[:500],
        )

    resultado = ResultadoFuente(
        source="yahoo_fundamentals", categoria="fundamentals", registros=registros
    )
    if fallidos:
        resultado.registros.append(
            {"_parciales": True, "tickers_fallidos": fallidos, "source": "yahoo_fundamentals"}
        )
    return resultado


def _fila_de(ticker: str, periodo: Any, estados: dict[str, Any]) -> dict[str, Any] | None:
    """Une los campos de los tres estados para un (ticker, periodo).

    Devuelve None si ningún campo trajo valor — no tiene sentido persistir una
    fila completamente vacía, y el contrato la rechazaría igual.
    """
    fila: dict[str, Any] = {
        "ticker": ticker,
        "period_end": periodo.date().isoformat(),
        "source": "yahoo_fundamentals",
    }
    algun_valor = False
    for df in estados.values():
        if df is None or df.empty or periodo not in df.columns:
            continue
        for campo_yf, campo_propio in TODOS_LOS_CAMPOS.items():
            if campo_propio in fila:
                continue  # ya lo trajo otro estado (no debería colisionar, pero por si acaso)
            if campo_yf in df.index:
                valor = _num(df.loc[campo_yf, periodo])
                if valor is not None:
                    algun_valor = True
                fila[campo_propio] = valor

    if not algun_valor:
        return None
    return fila


def _num(valor: Any) -> float | None:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return None if numero != numero else numero  # NaN
