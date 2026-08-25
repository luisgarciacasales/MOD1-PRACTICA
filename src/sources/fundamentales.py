"""Estados financieros vía yfinance — trimestral y anual (ampliación
14-ago-2026, extendida 25-ago-2026 con la serie anual).

Mismo patrón que `market.py`: fail-soft por ticker además de por fuente, y
pausa entre solicitudes para no maltratar el rate limit de Yahoo. La
diferencia es que aquí se piden TRES estados por ticker (resultados, balance,
flujo) en vez de un histórico de precios — de ahí que la pausa se aplique una
vez por ticker, no por estado.

AMPLIACIÓN 25-ago-2026 (roadmap F1, profundidad de datos): `quarterly_income_stmt`
y equivalentes tienen un tope duro de Yahoo — verificado el 25-ago contra
GFNORTEO.MX, exactamente 7 trimestres (2024-12-31→2026-06-30) tanto con el
`DataFrame` directo como con `get_income_stmt(freq="quarterly")`; no hay
parámetro que pida más. Los estados ANUALES (`t.income_stmt` sin `quarterly_`)
son una llamada distinta con su propio tope, y ese sí llega más atrás: 5 años
(2021-2025) para la misma emisora el mismo día. `ingerir_anual` pide esa serie
para ganar profundidad histórica real donde la trimestral no puede darla —
va a `silver_fundamentals_anual`/`gold_fundamentals_anual`, tablas separadas
de las trimestrales (decisión del usuario, 25-ago-2026): un reporte anual y el
trimestre Q4 del mismo ejercicio comparten `period_end` pero son magnitudes
distintas (el año completo vs. solo el último trimestre), y mezclarlos en la
misma tabla habría corrompido la clave de idempotencia `(ticker, period_end)`
ya probada en producción.
"""

from __future__ import annotations

import time
from typing import Any

from src.config.fundamentales import TICKERS_FUNDAMENTALES, TODOS_LOS_CAMPOS
from src.sources.base import ResultadoFuente

PAUSA_ENTRE_TICKERS = 0.6


def ingerir(*, tickers: tuple[str, ...] = TICKERS_FUNDAMENTALES) -> ResultadoFuente:
    """Descarga los tres estados financieros TRIMESTRALES de cada ticker.

    Fail-soft por ticker: que yfinance no tenga estados de una emisora (o solo
    parcialmente) no puede costar las demás. Se declara caída la fuente solo si
    ningún ticker devolvió un solo trimestre utilizable.
    """
    return _ingerir_estados(
        tickers=tickers,
        fuente="yahoo_fundamentals",
        campos_df=lambda t: {
            "income": t.quarterly_income_stmt,
            "balance": t.quarterly_balance_sheet,
            "cashflow": t.quarterly_cashflow,
        },
        etiqueta_error="trimestre",
    )


def ingerir_anual(*, tickers: tuple[str, ...] = TICKERS_FUNDAMENTALES) -> ResultadoFuente:
    """Descarga los tres estados financieros ANUALES de cada ticker.

    Mismos campos y misma unión que `ingerir`, contra el histórico anual de
    yfinance en vez del trimestral — ver la nota de ampliación del módulo
    para el porqué (profundidad: 5 años vs. 7 trimestres fijos).
    """
    return _ingerir_estados(
        tickers=tickers,
        fuente="yahoo_fundamentals_anual",
        campos_df=lambda t: {
            "income": t.income_stmt,
            "balance": t.balance_sheet,
            "cashflow": t.cashflow,
        },
        etiqueta_error="ejercicio",
    )


def _ingerir_estados(
    *,
    tickers: tuple[str, ...],
    fuente: str,
    campos_df: Any,
    etiqueta_error: str,
) -> ResultadoFuente:
    try:
        import yfinance as yf
    except Exception as exc:  # noqa: BLE001
        return ResultadoFuente.fallo(fuente, "fundamentals", exc)

    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, ticker in enumerate(tickers):
        try:
            estados = campos_df(yf.Ticker(ticker))
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
                fila = _fila_de(ticker, periodo, estados, fuente=fuente)
                if fila is not None:
                    registros.append(fila)
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{ticker}: {type(exc).__name__}")

        if i < len(tickers) - 1:
            time.sleep(PAUSA_ENTRE_TICKERS)

    if not registros:
        return ResultadoFuente(
            source=fuente,
            categoria="fundamentals",
            error=f"ningún ticker devolvió estados por {etiqueta_error} ({'; '.join(fallidos)})"[:500],
        )

    resultado = ResultadoFuente(source=fuente, categoria="fundamentals", registros=registros)
    if fallidos:
        resultado.registros.append(
            {"_parciales": True, "tickers_fallidos": fallidos, "source": fuente}
        )
    return resultado


def _fila_de(
    ticker: str, periodo: Any, estados: dict[str, Any], *, fuente: str
) -> dict[str, Any] | None:
    """Une los campos de los tres estados para un (ticker, periodo).

    Devuelve None si ningún campo trajo valor — no tiene sentido persistir una
    fila completamente vacía, y el contrato la rechazaría igual.
    """
    fila: dict[str, Any] = {
        "ticker": ticker,
        "period_end": periodo.date().isoformat(),
        "source": fuente,
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
