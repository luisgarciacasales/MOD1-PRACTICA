"""Campos de estados financieros trimestrales vía yfinance.

Origen de la petición del usuario (14-ago-2026): ampliar el corpus con
reportes trimestrales de las emisoras públicas (Banorte, etc.). Se evaluaron
dos caminos —cifras estructuradas vs. el comunicado narrativo de cada
empresa— y se decidió empezar por el primero: yfinance ya expone los tres
estados financieros trimestrales (resultados, balance, flujo de efectivo) para
cada ticker, con la misma librería que ya usamos para precios. El narrativo
(texto del reporte, análisis de la administración) queda pendiente de
evaluación — no tiene API común, requeriría un scraper por emisora con el
mismo riesgo de bloqueo que El Economista.

Verificado contra GFNORTEO.MX el 14-ago-2026: 7 trimestres de historia, con
huecos NaN dispersos (no todas las emisoras reportan los mismos campos cada
trimestre) — de ahí que el contrato trate cada campo como opcional y solo
rechace la fila si NINGÚN campo trae valor.

Los nombres de yfinance (columna izquierda) son los que usa `DataFrame.index`
de `Ticker.quarterly_income_stmt` / `quarterly_balance_sheet` /
`quarterly_cashflow`; el nombre propio (derecha) es el que viaja al contrato
`Fundamental` y a `silver_fundamentals`.
"""

from __future__ import annotations

from src.config.tickers import TICKERS_PRIORITARIOS

CAMPOS_INCOME: dict[str, str] = {
    "Total Revenue": "ingresos_totales",
    "Net Income": "utilidad_neta",
    "Diluted EPS": "utilidad_por_accion",
}

CAMPOS_BALANCE: dict[str, str] = {
    "Total Assets": "activo_total",
    "Total Liabilities Net Minority Interest": "pasivo_total",
    "Stockholders Equity": "capital_contable",
}

CAMPOS_CASHFLOW: dict[str, str] = {
    "Operating Cash Flow": "flujo_operativo",
    "Free Cash Flow": "flujo_libre",
}

# Unión de las tres, para iterar sin distinguir de qué estado vino cada campo.
TODOS_LOS_CAMPOS: dict[str, str] = {**CAMPOS_INCOME, **CAMPOS_BALANCE, **CAMPOS_CASHFLOW}

# Mismo universo que precios, sin el benchmark (un índice no reporta estados
# financieros).
TICKERS_FUNDAMENTALES: tuple[str, ...] = TICKERS_PRIORITARIOS
