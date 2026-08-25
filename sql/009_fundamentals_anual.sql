-- Ampliación 25-ago-2026 (roadmap F1, profundidad de datos): estados
-- financieros ANUALES, en tablas separadas de las trimestrales de
-- 007_fundamentals.sql. Ver src/sources/fundamentales.py (ingerir_anual) para
-- el porqué de la profundidad (5 años vs. 7 trimestres fijos de Yahoo) y
-- src/contracts/fundamentals.py para el porqué de tablas separadas en vez de
-- una columna period_type: un reporte anual y el trimestre Q4 del mismo
-- ejercicio comparten period_end pero son magnitudes distintas, y mezclarlos
-- habría corrompido la clave de idempotencia (ticker, period_end) que ya
-- corre en producción sobre silver_fundamentals.

-- ---------------------------------------------------------------------------
-- silver_fundamentals_anual — resultados, balance y flujo por ejercicio
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_fundamentals_anual (
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

    CONSTRAINT silver_fundamentals_anual_unico UNIQUE (ticker, period_end),
    -- Mismas cotas que silver_fundamentals — mismo contrato Pydantic, misma
    -- defensa en profundidad.
    CONSTRAINT silver_fundamentals_anual_activo_positivo CHECK (activo_total IS NULL OR activo_total > 0),
    CONSTRAINT silver_fundamentals_anual_pasivo_no_negativo CHECK (pasivo_total IS NULL OR pasivo_total >= 0),
    CONSTRAINT silver_fundamentals_anual_al_menos_un_campo CHECK (
        ingresos_totales IS NOT NULL OR utilidad_neta IS NOT NULL OR
        utilidad_por_accion IS NOT NULL OR activo_total IS NOT NULL OR
        pasivo_total IS NOT NULL OR capital_contable IS NOT NULL OR
        flujo_operativo IS NOT NULL OR flujo_libre IS NOT NULL
    )
);

CREATE INDEX IF NOT EXISTS idx_fundamentals_anual_ticker_periodo
    ON silver_fundamentals_anual (ticker, period_end DESC);

-- ---------------------------------------------------------------------------
-- gold_fundamentals_anual — con crecimiento interanual de ingresos y utilidad
-- ---------------------------------------------------------------------------
-- El YoY anual compara contra el ejercicio inmediato anterior sin la
-- ambigüedad de calendario que puede tener el trimestral (un trimestre puede
-- llegar tarde o faltar); aun así se usa la misma ventana LATERAL >= 1 año en
-- vez de "el registro anterior" a secas, por consistencia con
-- _SQL_FUNDAMENTALES y por si algún ejercicio faltara en el histórico.
CREATE TABLE IF NOT EXISTS gold_fundamentals_anual (
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

    CONSTRAINT gold_fundamentals_anual_unico UNIQUE (ticker, period_end)
);

CREATE INDEX IF NOT EXISTS idx_gold_fundamentals_anual_ticker_periodo
    ON gold_fundamentals_anual (ticker, period_end DESC);
