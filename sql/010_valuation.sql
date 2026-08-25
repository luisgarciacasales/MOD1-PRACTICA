-- F2 (roadmap 25-ago-2026) — primer corte del motor de valuación relativa:
-- P/U histórico (no una foto de hoy) con z-score contra la propia historia de
-- cada emisora. Ver src/pipeline/transform.py (_SQL_VALUATION) para el cálculo
-- completo, incluida la razón del rezago de 45 días (evitar lookahead bias:
-- un trimestre no está disponible el mismo día en que cierra).
--
-- P/VL, EV/EBITDA y dividend yield quedan fuera de este corte a propósito:
-- cada uno necesita un dato que hoy no se ingiere (shares_outstanding, un
-- EBITDA limpio —débil para bancos, 9 de las 16 emisoras— y una serie de
-- dividendos, respectivamente). Ampliar es agregar esa fuente, no rediseñar
-- esta tabla.
CREATE TABLE IF NOT EXISTS gold_valuation (
    id                SERIAL      PRIMARY KEY,
    ticker            TEXT        NOT NULL,
    date              DATE        NOT NULL,

    adj_close         DOUBLE PRECISION NOT NULL,
    eps_ttm           DOUBLE PRECISION NOT NULL,
    pe_ratio          DOUBLE PRECISION NOT NULL,
    pe_zscore_1y      DOUBLE PRECISION,

    ingested_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_valuation_unico UNIQUE (ticker, date),
    -- eps_ttm > 0 es deliberado, no un descuido: con UPA TTM negativa o cero el
    -- P/U no tiene lectura económica (una pérdida no "cuesta X veces sí
    -- misma") y _SQL_VALUATION ya filtra esos casos antes de insertar.
    CONSTRAINT gold_valuation_eps_positivo CHECK (eps_ttm > 0),
    CONSTRAINT gold_valuation_pe_positivo CHECK (pe_ratio > 0)
);

CREATE INDEX IF NOT EXISTS idx_gold_valuation_ticker_fecha
    ON gold_valuation (ticker, date DESC);
