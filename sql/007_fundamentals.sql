-- Ampliación 14-ago-2026: estados financieros trimestrales (silver_fundamentals)
-- y sus métricas derivadas (gold_fundamentals), vía yfinance. Ver
-- src/config/fundamentales.py para el porqué y el mapeo de campos.

-- ---------------------------------------------------------------------------
-- silver_fundamentals — resultados, balance y flujo por trimestre
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_fundamentals (
    id                   SERIAL      PRIMARY KEY,
    ticker               TEXT        NOT NULL,
    period_end           DATE        NOT NULL,

    ingresos_totales     DOUBLE PRECISION,
    utilidad_neta        DOUBLE PRECISION,
    utilidad_por_accion  DOUBLE PRECISION,
    activo_total         DOUBLE PRECISION,
    pasivo_total         DOUBLE PRECISION,
    capital_contable     DOUBLE PRECISION,
    flujo_operativo      DOUBLE PRECISION,
    flujo_libre          DOUBLE PRECISION,

    ingested_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_batch_uuid       UUID        NOT NULL,

    CONSTRAINT silver_fundamentals_unico UNIQUE (ticker, period_end),
    -- Mismas cotas que el contrato Pydantic — defensa en profundidad, como en
    -- silver_market_prices.
    CONSTRAINT silver_fundamentals_activo_positivo CHECK (activo_total IS NULL OR activo_total > 0),
    CONSTRAINT silver_fundamentals_pasivo_no_negativo CHECK (pasivo_total IS NULL OR pasivo_total >= 0),
    CONSTRAINT silver_fundamentals_al_menos_un_campo CHECK (
        ingresos_totales IS NOT NULL OR utilidad_neta IS NOT NULL OR
        utilidad_por_accion IS NOT NULL OR activo_total IS NOT NULL OR
        pasivo_total IS NOT NULL OR capital_contable IS NOT NULL OR
        flujo_operativo IS NOT NULL OR flujo_libre IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_ticker_periodo
    ON silver_fundamentals (ticker, period_end DESC);

-- ---------------------------------------------------------------------------
-- gold_fundamentals — con crecimiento interanual de ingresos y utilidad neta
-- ---------------------------------------------------------------------------
-- Solo estas dos métricas llevan YoY (no las ocho): son las que de verdad se
-- usan para leer competitividad trimestre a trimestre. Ampliar a las demás es
-- una extensión mecánica de `_SQL_FUNDAMENTALES` en transform.py si hiciera falta.
CREATE TABLE IF NOT EXISTS gold_fundamentals (
    id                        SERIAL      PRIMARY KEY,
    ticker                    TEXT        NOT NULL,
    period_end                DATE        NOT NULL,

    ingresos_totales          DOUBLE PRECISION,
    utilidad_neta             DOUBLE PRECISION,
    utilidad_por_accion       DOUBLE PRECISION,
    activo_total              DOUBLE PRECISION,
    pasivo_total              DOUBLE PRECISION,
    capital_contable          DOUBLE PRECISION,
    flujo_operativo           DOUBLE PRECISION,
    flujo_libre               DOUBLE PRECISION,

    ingresos_yoy_pct          DOUBLE PRECISION,
    utilidad_neta_yoy_pct     DOUBLE PRECISION,

    ingested_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_fundamentals_unico UNIQUE (ticker, period_end)
);

CREATE INDEX IF NOT EXISTS idx_gold_fundamentals_ticker_periodo
    ON gold_fundamentals (ticker, period_end DESC);
