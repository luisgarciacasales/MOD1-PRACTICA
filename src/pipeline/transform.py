"""Etapa 5 — Transformación Gold de datos de mercado. PRD §4.4 paso 5.

Precios: retornos diarios, medias móviles 7/30 y volatilidad 30.
Macro: nombre legible de la serie y variación interanual.
Valuación (F2, roadmap 25-ago-2026): P/U histórico con z-score de 1 año
contra la propia historia de cada emisora — ver `_SQL_VALUATION`.

Se hace en SQL con *window functions*, no en pandas: el PRD §7 lo especifica
así y evita traer 4 000 filas a Python para devolverlas acto seguido.

    docker compose exec -T app python -m src.pipeline.transform
"""

from __future__ import annotations

import argparse
import json

from src.config.banxico_series import SERIES_POR_ID
from src.config.inegi_series import INDICADORES_POR_ID
from src.config.tickers import BENCHMARK, TICKERS_MONEDA_FINANCIERA_DISTINTA
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

# YoY solo para ingresos y utilidad neta (no las ocho columnas): son las dos
# que de verdad se leen trimestre a trimestre para juzgar competitividad. El
# mismo patrón LATERAL de _SQL_MACRO — comparar contra la observación más
# reciente que sea al menos un año anterior, no contra "el mismo trimestre
# calendario", porque un reporte puede llegar tarde o faltar un trimestre.
_SQL_FUNDAMENTALES = """
INSERT INTO gold_fundamentals (
    ticker, period_end, ingresos_totales, utilidad_neta, utilidad_por_accion,
    activo_total, pasivo_total, capital_contable, flujo_operativo, flujo_libre,
    ingresos_yoy_pct, utilidad_neta_yoy_pct, ingested_at
)
SELECT s.ticker, s.period_end, s.ingresos_totales, s.utilidad_neta,
       s.utilidad_por_accion, s.activo_total, s.pasivo_total,
       s.capital_contable, s.flujo_operativo, s.flujo_libre,
       CASE
           WHEN prev.ingresos_totales IS NULL OR prev.ingresos_totales = 0 THEN NULL
           ELSE 100.0 * (s.ingresos_totales / prev.ingresos_totales - 1)
       END,
       CASE
           WHEN prev.utilidad_neta IS NULL OR prev.utilidad_neta = 0 THEN NULL
           ELSE 100.0 * (s.utilidad_neta / prev.utilidad_neta - 1)
       END,
       NOW()
FROM silver_fundamentals s
LEFT JOIN LATERAL (
    SELECT p.ingresos_totales, p.utilidad_neta
    FROM silver_fundamentals p
    WHERE p.ticker = s.ticker
      AND p.period_end <= s.period_end - INTERVAL '1 year'
    ORDER BY p.period_end DESC
    LIMIT 1
) prev ON TRUE
ON CONFLICT (ticker, period_end) DO UPDATE SET
    ingresos_totales      = EXCLUDED.ingresos_totales,
    utilidad_neta         = EXCLUDED.utilidad_neta,
    utilidad_por_accion   = EXCLUDED.utilidad_por_accion,
    activo_total          = EXCLUDED.activo_total,
    pasivo_total          = EXCLUDED.pasivo_total,
    capital_contable      = EXCLUDED.capital_contable,
    flujo_operativo       = EXCLUDED.flujo_operativo,
    flujo_libre           = EXCLUDED.flujo_libre,
    ingresos_yoy_pct      = EXCLUDED.ingresos_yoy_pct,
    utilidad_neta_yoy_pct = EXCLUDED.utilidad_neta_yoy_pct,
    ingested_at           = NOW()
RETURNING (xmax = 0)
"""

# Misma consulta que _SQL_FUNDAMENTALES sobre la tabla anual (ver
# sql/009_fundamentals_anual.sql) — se escribe completa en vez de derivarla por
# sustitución de texto, mismo criterio que separar silver_fundamentals de
# silver_market_prices en vez de parametrizar una única consulta genérica. El
# YoY compara contra el ejercicio anterior con la misma ventana LATERAL >= 1
# año, por si algún ejercicio faltara en el histórico.
_SQL_FUNDAMENTALES_ANUAL = """
INSERT INTO gold_fundamentals_anual (
    ticker, period_end, ingresos_totales, utilidad_neta, utilidad_por_accion,
    activo_total, pasivo_total, capital_contable, flujo_operativo, flujo_libre,
    ingresos_yoy_pct, utilidad_neta_yoy_pct, ingested_at
)
SELECT s.ticker, s.period_end, s.ingresos_totales, s.utilidad_neta,
       s.utilidad_por_accion, s.activo_total, s.pasivo_total,
       s.capital_contable, s.flujo_operativo, s.flujo_libre,
       CASE
           WHEN prev.ingresos_totales IS NULL OR prev.ingresos_totales = 0 THEN NULL
           ELSE 100.0 * (s.ingresos_totales / prev.ingresos_totales - 1)
       END,
       CASE
           WHEN prev.utilidad_neta IS NULL OR prev.utilidad_neta = 0 THEN NULL
           ELSE 100.0 * (s.utilidad_neta / prev.utilidad_neta - 1)
       END,
       NOW()
FROM silver_fundamentals_anual s
LEFT JOIN LATERAL (
    SELECT p.ingresos_totales, p.utilidad_neta
    FROM silver_fundamentals_anual p
    WHERE p.ticker = s.ticker
      AND p.period_end <= s.period_end - INTERVAL '1 year'
    ORDER BY p.period_end DESC
    LIMIT 1
) prev ON TRUE
ON CONFLICT (ticker, period_end) DO UPDATE SET
    ingresos_totales      = EXCLUDED.ingresos_totales,
    utilidad_neta         = EXCLUDED.utilidad_neta,
    utilidad_por_accion   = EXCLUDED.utilidad_por_accion,
    activo_total          = EXCLUDED.activo_total,
    pasivo_total          = EXCLUDED.pasivo_total,
    capital_contable      = EXCLUDED.capital_contable,
    flujo_operativo       = EXCLUDED.flujo_operativo,
    flujo_libre           = EXCLUDED.flujo_libre,
    ingresos_yoy_pct      = EXCLUDED.ingresos_yoy_pct,
    utilidad_neta_yoy_pct = EXCLUDED.utilidad_neta_yoy_pct,
    ingested_at           = NOW()
RETURNING (xmax = 0)
"""


# F2 (roadmap 25-ago-2026) — P/U histórico con z-score contra la propia
# historia de la emisora. Ver sql/010_valuation.sql para qué queda fuera de
# este corte (P/VL, EV/EBITDA, dividend yield) y por qué.
#
# REZAGO_PUBLICACION_DIAS: un trimestre no está disponible el día que cierra
# — se publica semanas después. Usar period_end sin rezago sería lookahead
# bias: el P/U de una fecha usaría una UPA que en la realidad todavía no
# existía ese día. 45 días es una aproximación (mismo espíritu que la
# aproximación de fecha ya documentada en gold_fundamentals — "declarada,
# no fingida"), no un dato verificado emisora por emisora.
#
# eps_ttm exige 4 trimestres consecutivos (ROWS BETWEEN 3 PRECEDING): una UPA
# TTM de 1-2 trimestres subestimaría el denominador y produciría un P/U
# falsamente alto.
#
# El z-score exige al menos 60 sesiones de historia (~3 meses), no las ~252
# de un año completo: a diferencia de ma_30d/volatility_30d en _SQL_PRECIOS
# —donde exigir el conteo exacto evita ETIQUETAR 3 días como "media de 30"—,
# aquí un z-score sobre una ventana parcial sigue siendo un z-score válido,
# solo con menos grados de libertad. Negarlo hasta el día 252 dejaría sin
# lectura todo el primer año de cada emisora.
#
# Respaldo anual (011_valuation_eps_anual.sql, descubierto al desplegar): la
# UPA trimestral de Yahoo trae huecos estructurales en el sector financiero
# (Q2 y a veces Q3 en blanco para Banorte, Inbursa, Quálitas, GFinbur — ni
# con "Basic EPS" como alternativa, mismo patrón de NULL). Sin eps_anual como
# respaldo esas emisoras —justo la prioridad del roadmap— no tendrían P/U
# nunca. eps_source declara cuál se usó: no son la misma medida (anual
# actualiza 1 vez al año, TTM trimestral 4 veces).
#
# Exclusión de moneda (TICKERS_MONEDA_FINANCIERA_DISTINTA, ver
# src/config/tickers.py): CEMEXCPO.MX/GMEXICOB.MX reportan en USD, BBVA.MX/
# SANN.MX en EUR, todas con precio en MXN — sin excluirlas el P/U sale en
# cientos de veces, un artefacto de conversión, no una lectura real.
_SQL_VALUATION = """
WITH eps_ttm AS (
    SELECT ticker, period_end,
           SUM(utilidad_por_accion) OVER w AS eps_ttm,
           COUNT(utilidad_por_accion) OVER w AS n_trimestres,
           period_end + 45 AS disponible_desde
    FROM silver_fundamentals
    WINDOW w AS (PARTITION BY ticker ORDER BY period_end ROWS BETWEEN 3 PRECEDING AND CURRENT ROW)
),
-- Mismo rezago de 45 días que el trimestral: el reporte anual de una
-- financiera suele publicarse junto con su Q4, mismo calendario de
-- disclosure — no es una segunda aproximación independiente.
eps_anual AS (
    SELECT ticker, period_end, utilidad_por_accion AS eps_anual,
           period_end + 45 AS disponible_desde
    FROM silver_fundamentals_anual
    WHERE utilidad_por_accion IS NOT NULL
),
precio_eps AS (
    SELECT p.ticker, p.date, p.adj_close,
           COALESCE(t.eps_ttm, a.eps_anual) AS eps_ttm,
           CASE WHEN t.eps_ttm IS NOT NULL THEN 'trimestral_ttm' ELSE 'anual' END AS eps_source
    FROM silver_market_prices p
    LEFT JOIN LATERAL (
        SELECT eps.eps_ttm
        FROM eps_ttm eps
        WHERE eps.ticker = p.ticker
          AND eps.n_trimestres = 4
          AND eps.disponible_desde <= p.date
        ORDER BY eps.period_end DESC
        LIMIT 1
    ) t ON TRUE
    LEFT JOIN LATERAL (
        SELECT ea.eps_anual
        FROM eps_anual ea
        WHERE ea.ticker = p.ticker
          AND ea.disponible_desde <= p.date
        ORDER BY ea.period_end DESC
        LIMIT 1
    ) a ON TRUE
    WHERE p.ticker <> %(benchmark)s  -- un índice no tiene UPA
      AND p.ticker <> ALL(%(moneda_distinta)s)
),
con_pe AS (
    SELECT ticker, date, adj_close, eps_ttm, eps_source,
           CASE WHEN eps_ttm > 0 THEN adj_close / eps_ttm END AS pe_ratio
    FROM precio_eps
),
con_z AS (
    SELECT *,
           AVG(pe_ratio)        OVER w AS pe_media_1y,
           STDDEV_SAMP(pe_ratio) OVER w AS pe_desv_1y,
           COUNT(pe_ratio)      OVER w AS n_1y
    FROM con_pe
    WINDOW w AS (PARTITION BY ticker ORDER BY date ROWS BETWEEN 251 PRECEDING AND CURRENT ROW)
)
INSERT INTO gold_valuation (
    ticker, date, adj_close, eps_ttm, eps_source, pe_ratio, pe_zscore_1y, ingested_at
)
SELECT ticker, date, adj_close, eps_ttm, eps_source, pe_ratio,
       CASE WHEN n_1y >= 60 AND pe_desv_1y > 0
            THEN (pe_ratio - pe_media_1y) / pe_desv_1y END,
       NOW()
FROM con_z
WHERE pe_ratio IS NOT NULL
ON CONFLICT (ticker, date) DO UPDATE SET
    adj_close    = EXCLUDED.adj_close,
    eps_ttm      = EXCLUDED.eps_ttm,
    eps_source   = EXCLUDED.eps_source,
    pe_ratio     = EXCLUDED.pe_ratio,
    pe_zscore_1y = EXCLUDED.pe_zscore_1y,
    ingested_at  = NOW()
RETURNING (xmax = 0)
"""

# Limpieza de lo que _SQL_VALUATION ya no visita: las filas de
# TICKERS_MONEDA_FINANCIERA_DISTINTA que se insertaron ANTES de que existiera
# la exclusión de moneda (25-ago-2026) se quedarían inertes en Gold —
# artefactos correctos en su momento, erróneos ahora — porque un
# ON CONFLICT solo actualiza filas que la nueva consulta vuelve a visitar.
_SQL_VALUATION_LIMPIAR = "DELETE FROM gold_valuation WHERE ticker = ANY(%(moneda_distinta)s)"


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

        cur.execute(_SQL_FUNDAMENTALES)
        f_nuevas, f_act = _contar(cur)

        cur.execute(_SQL_FUNDAMENTALES_ANUAL)
        fa_nuevas, fa_act = _contar(cur)

        moneda_distinta = list(TICKERS_MONEDA_FINANCIERA_DISTINTA)
        cur.execute(_SQL_VALUATION_LIMPIAR, {"moneda_distinta": moneda_distinta})
        cur.execute(_SQL_VALUATION, {"benchmark": BENCHMARK, "moneda_distinta": moneda_distinta})
        v_nuevas, v_act = _contar(cur)
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

        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE ingresos_yoy_pct IS NOT NULL) FROM gold_fundamentals"
        )
        con_fund_yoy = cur.fetchone()[0]

        cur.execute(
            "SELECT COUNT(*) FILTER (WHERE ingresos_yoy_pct IS NOT NULL) FROM gold_fundamentals_anual"
        )
        con_fund_anual_yoy = cur.fetchone()[0]

        cur.execute("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE pe_zscore_1y IS NOT NULL),
                   COUNT(*) FILTER (WHERE eps_source = 'anual')
            FROM gold_valuation
        """)
        con_pe, con_pe_z, con_pe_anual = cur.fetchone()

    print(f"{'TABLA':<24} {'NUEVAS':>8} {'ACTUALIZ':>9}")
    print("-" * 43)
    print(f"{'gold_market_prices':<24} {p_nuevas:>8} {p_act:>9}")
    print(f"{'gold_macro_indicators':<24} {m_nuevas:>8} {m_act:>9}")
    print(f"{'gold_fundamentals':<24} {f_nuevas:>8} {f_act:>9}")
    print(f"{'gold_fundamentals_anual':<24} {fa_nuevas:>8} {fa_act:>9}")
    print(f"{'gold_valuation':<24} {v_nuevas:>8} {v_act:>9}")
    print("-" * 43)
    print()
    print("cobertura de las métricas derivadas:")
    print(f"  daily_return_pct  {con_ret}")
    print(f"  ma_7d             {con_m7}")
    print(f"  ma_30d            {con_m30}")
    print(f"  volatility_30d    {con_vol}")
    print(f"  excess_return_pct {con_exc}   (frente al benchmark {BENCHMARK})")
    print(f"  beta_60d          {con_beta}")
    print(f"  yoy_change_pct    {con_yoy}   (macro)")
    print(f"  ingresos_yoy_pct  {con_fund_yoy}   (fundamentales trimestrales)")
    print(f"  ingresos_yoy_pct  {con_fund_anual_yoy}   (fundamentales anuales)")
    print(f"  pe_ratio          {con_pe}   (F2, {con_pe_z} con z-score de 1 año, "
          f"{con_pe_anual} con UPA anual de respaldo)")
    print()
    print(f"[transform] filas_nuevas = {p_nuevas + m_nuevas + f_nuevas + fa_nuevas + v_nuevas}  "
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
