"""Etapa 5 — Transformación Gold de datos de mercado. PRD §4.4 paso 5.

Precios: retornos diarios, medias móviles 7/30 y volatilidad 30.
Macro: nombre legible de la serie y variación interanual.

Se hace en SQL con *window functions*, no en pandas: el PRD §7 lo especifica
así y evita traer 4 000 filas a Python para devolverlas acto seguido.

    docker compose exec -T app python -m src.pipeline.transform
"""

from __future__ import annotations

import argparse
import json

from src.config.banxico_series import SERIES_POR_ID
from src.config.inegi_series import INDICADORES_POR_ID
from src.config.tickers import BENCHMARK
from src.pipeline import db

# Dos decisiones dentro de esta consulta que conviene no perder de vista:
#
# 1. Todo se calcula sobre `adj_close`, no sobre `close`. Un split o un
#    dividendo produce un salto en `close` que se leería como un retorno real
#    del -50%, contaminando la volatilidad y las medias durante 30 sesiones.
#
# 2. Las medias móviles se anulan hasta que la ventana está completa. Sin ese
#    corte, `ma_30d` en la sesión 3 sería una media de 3 días presentándose
#    como una de 30 — un dato que parece válido y no lo es.
_SQL_PRECIOS = """
WITH base AS (
    SELECT ticker, date, open, high, low, close, adj_close, volume,
           100.0 * (
               adj_close / NULLIF(LAG(adj_close) OVER v, 0) - 1
           ) AS ret
    FROM silver_market_prices
    WINDOW v AS (PARTITION BY ticker ORDER BY date)
),
-- El retorno del benchmark, extraído de la misma CTE para que se calcule
-- exactamente igual que el de las emisoras.
bench AS (
    SELECT date, ret AS ret_bench FROM base WHERE ticker = %(benchmark)s
),
calc AS (
    SELECT b.*, k.ret_bench,
        AVG(b.adj_close)   OVER (v ROWS BETWEEN  6 PRECEDING AND CURRENT ROW) AS m7,
        AVG(b.adj_close)   OVER (v ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS m30,
        STDDEV_SAMP(b.ret) OVER (v ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS vol30,
        COUNT(*)           OVER (v ROWS BETWEEN  6 PRECEDING AND CURRENT ROW) AS n7,
        COUNT(*)           OVER (v ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS n30,
        -- Beta = covarianza(emisora, índice) / varianza(índice) sobre 60
        -- sesiones. COVAR_SAMP y VAR_SAMP son agregados, y PostgreSQL admite
        -- usar agregados como funciones de ventana, así que la beta móvil sale
        -- en la misma pasada sin subconsultas correlacionadas.
        COVAR_SAMP(b.ret, k.ret_bench) OVER (v ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            AS cov60,
        VAR_SAMP(k.ret_bench)          OVER (v ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            AS var60,
        CORR(b.ret, k.ret_bench)       OVER (v ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            AS corr60,
        COUNT(k.ret_bench)             OVER (v ROWS BETWEEN 59 PRECEDING AND CURRENT ROW)
            AS n60
    FROM base b
    -- LEFT JOIN: si el índice no publicó ese día, la emisora conserva su
    -- retorno absoluto y las métricas relativas quedan NULL. Un INNER JOIN
    -- borraría la fila entera y perdería el precio.
    LEFT JOIN bench k ON k.date = b.date
    WINDOW v AS (PARTITION BY b.ticker ORDER BY b.date)
)
INSERT INTO gold_market_prices (
    ticker, date, open, high, low, close, adj_close, volume,
    daily_return_pct, ma_7d, ma_30d, volatility_30d,
    benchmark_return_pct, excess_return_pct, beta_60d, correlacion_60d,
    ingested_at
)
SELECT ticker, date, open, high, low, close, adj_close, volume,
       ret,
       CASE WHEN n7  = 7  THEN m7  END,
       CASE WHEN n30 = 30 THEN m30 END,
       CASE WHEN n30 = 30 THEN vol30 END,
       -- Métricas relativas. Se anulan en la fila del propio índice: su exceso
       -- es 0 y su beta 1 por definición, y dejarlas distorsionaría cualquier
       -- agregación sobre el conjunto de emisoras.
       CASE WHEN ticker <> %(benchmark)s THEN ret_bench END,
       CASE WHEN ticker <> %(benchmark)s THEN ret - ret_bench END,
       CASE WHEN ticker <> %(benchmark)s AND n60 = 60
            THEN cov60 / NULLIF(var60, 0) END,
       CASE WHEN ticker <> %(benchmark)s AND n60 = 60 THEN corr60 END,
       NOW()
FROM calc
ON CONFLICT (ticker, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, adj_close = EXCLUDED.adj_close, volume = EXCLUDED.volume,
    daily_return_pct = EXCLUDED.daily_return_pct,
    ma_7d = EXCLUDED.ma_7d, ma_30d = EXCLUDED.ma_30d,
    volatility_30d = EXCLUDED.volatility_30d,
    benchmark_return_pct = EXCLUDED.benchmark_return_pct,
    excess_return_pct = EXCLUDED.excess_return_pct,
    beta_60d = EXCLUDED.beta_60d,
    correlacion_60d = EXCLUDED.correlacion_60d,
    ingested_at = NOW()
RETURNING (xmax = 0)
"""

# La variación interanual se compara contra la observación más reciente que sea
# al menos un año anterior, no contra "la misma fecha del año pasado": esa
# fecha puede ser festivo o simplemente no existir en una serie mensual.
_SQL_MACRO = """
INSERT INTO gold_macro_indicators (
    series_id, series_name, date, value, yoy_change_pct, ingested_at
)
SELECT s.series_id,
       COALESCE(%(nombres)s::jsonb ->> s.series_id, s.series_id),
       s.date,
       s.value,
       CASE
           WHEN prev.value IS NULL OR prev.value = 0 THEN NULL
           ELSE 100.0 * (s.value / prev.value - 1)
       END,
       NOW()
FROM silver_macro_indicators s
LEFT JOIN LATERAL (
    SELECT p.value
    FROM silver_macro_indicators p
    WHERE p.series_id = s.series_id
      AND p.date <= s.date - INTERVAL '1 year'
    ORDER BY p.date DESC
    LIMIT 1
) prev ON TRUE
ON CONFLICT (series_id, date) DO UPDATE SET
    series_name = EXCLUDED.series_name,
    value = EXCLUDED.value,
    yoy_change_pct = EXCLUDED.yoy_change_pct,
    ingested_at = NOW()
RETURNING (xmax = 0)
"""


def _contar(cur) -> tuple[int, int]:
    """Separa inserciones de actualizaciones con el `RETURNING (xmax = 0)`."""
    filas = cur.fetchall()
    nuevas = sum(1 for f in filas if f[0])
    return nuevas, len(filas) - nuevas


def ejecutar() -> int:
    # Los nombres de las dos fuentes macro comparten el mapeo porque comparten
    # tabla. Sin esto, un indicador del INEGI se guardaría con su id numérico
    # como nombre — el mismo síntoma que delató las series mal etiquetadas.
    nombres = json.dumps(
        {s.id: s.nombre for s in SERIES_POR_ID.values()}
        | {i.id: i.nombre for i in INDICADORES_POR_ID.values()}
    )

    with db.conectar() as conexion, conexion.cursor() as cur:
        cur.execute(_SQL_PRECIOS, {"benchmark": BENCHMARK})
        p_nuevas, p_act = _contar(cur)

        cur.execute(_SQL_MACRO, {"nombres": nombres})
        m_nuevas, m_act = _contar(cur)
        conexion.commit()

        cur.execute("""
            SELECT COUNT(*) FILTER (WHERE daily_return_pct IS NOT NULL),
                   COUNT(*) FILTER (WHERE ma_7d IS NOT NULL),
                   COUNT(*) FILTER (WHERE ma_30d IS NOT NULL),
                   COUNT(*) FILTER (WHERE volatility_30d IS NOT NULL),
                   COUNT(*) FILTER (WHERE excess_return_pct IS NOT NULL),
                   COUNT(*) FILTER (WHERE beta_60d IS NOT NULL)
            FROM gold_market_prices
        """)
        con_ret, con_m7, con_m30, con_vol, con_exc, con_beta = cur.fetchone()

        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE yoy_change_pct IS NOT NULL) FROM gold_macro_indicators"
        )
        con_yoy = cur.fetchone()[0]

    print(f"{'TABLA':<24} {'NUEVAS':>8} {'ACTUALIZ':>9}")
    print("-" * 43)
    print(f"{'gold_market_prices':<24} {p_nuevas:>8} {p_act:>9}")
    print(f"{'gold_macro_indicators':<24} {m_nuevas:>8} {m_act:>9}")
    print("-" * 43)
    print()
    print("cobertura de las métricas derivadas:")
    print(f"  daily_return_pct  {con_ret}")
    print(f"  ma_7d             {con_m7}")
    print(f"  ma_30d            {con_m30}")
    print(f"  volatility_30d    {con_vol}")
    print(f"  excess_return_pct {con_exc}   (frente al benchmark {BENCHMARK})")
    print(f"  beta_60d          {con_beta}")
    print(f"  yoy_change_pct    {con_yoy}")
    print()
    print(f"[transform] filas_nuevas = {p_nuevas + m_nuevas}  "
          f"(reprocesar debe dar 0 — criterio PRD §8)")
    return 0


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="src.pipeline.transform",
        description="Métricas derivadas de mercado y macro (Silver → Gold).",
    ).parse_args(argv)
    return ejecutar()


if __name__ == "__main__":
    raise SystemExit(main())
