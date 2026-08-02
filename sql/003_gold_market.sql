-- Capa Gold — mercado, macro y correlación (PRD §5.3).
--
-- Las escriben `src.pipeline.transform` (las dos primeras) y
-- `src.pipeline.correlate` (la tercera).

-- ---------------------------------------------------------------------------
-- gold_market_prices — precios con métricas derivadas
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_market_prices (
    id               SERIAL      PRIMARY KEY,
    ticker           TEXT        NOT NULL,
    date             DATE        NOT NULL,
    open             DOUBLE PRECISION NOT NULL,
    high             DOUBLE PRECISION NOT NULL,
    low              DOUBLE PRECISION NOT NULL,
    close            DOUBLE PRECISION NOT NULL,
    adj_close        DOUBLE PRECISION NOT NULL,
    volume           BIGINT      NOT NULL,

    -- Calculadas sobre adj_close, no sobre close: los splits y dividendos
    -- meten saltos artificiales en close que se leerían como retornos reales.
    daily_return_pct DOUBLE PRECISION,
    ma_7d            DOUBLE PRECISION,
    ma_30d           DOUBLE PRECISION,
    volatility_30d   DOUBLE PRECISION,

    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_market_prices_unico UNIQUE (ticker, date)
);

CREATE INDEX IF NOT EXISTS idx_gold_prices_ticker_fecha
    ON gold_market_prices (ticker, date DESC);

-- ---------------------------------------------------------------------------
-- gold_macro_indicators — series normalizadas
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_macro_indicators (
    id             SERIAL      PRIMARY KEY,
    series_id      TEXT        NOT NULL,
    series_name    TEXT        NOT NULL,
    date           DATE        NOT NULL,
    value          DOUBLE PRECISION NOT NULL,
    yoy_change_pct DOUBLE PRECISION,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_macro_indicators_unico UNIQUE (series_id, date)
);

CREATE INDEX IF NOT EXISTS idx_gold_macro_serie_fecha
    ON gold_macro_indicators (series_id, date DESC);

-- ---------------------------------------------------------------------------
-- gold_news_market_corr — JOIN temporal noticias ↔ precios (PRD §4.4 paso 6)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_news_market_corr (
    id                  SERIAL      PRIMARY KEY,
    news_guid           TEXT        NOT NULL REFERENCES gold_enriched_news(guid) ON DELETE CASCADE,

    -- Ticker de la BMV con el que se hizo el JOIN de precios. En una fila
    -- directa es la emisora detectada; en una fila proxy es la emisora que
    -- sustituye a la fintech. `proxy_ticker` repite ese valor cuando
    -- is_proxy — redundancia que pide el PRD §5.3 para que una consulta pueda
    -- filtrar proxies sin releer is_proxy.
    ticker              TEXT        NOT NULL,
    is_proxy            BOOLEAN     NOT NULL DEFAULT FALSE,
    proxy_ticker        TEXT,
    original_fintech    TEXT,
    sector_affected     TEXT,

    news_date           DATE        NOT NULL,
    -- Resuelto con el calendario XMEX, NO con fecha calendario (PRD §6.6):
    -- una noticia del viernes mide su impacto el lunes.
    next_trading_day    DATE        NOT NULL,
    price_date          DATE        NOT NULL,

    close_price         DOUBLE PRECISION,
    next_day_return_pct DOUBLE PRECISION,
    price_change_5d_pct DOUBLE PRECISION,
    -- Tasas y tipo de cambio vigentes en news_date. Sin este contexto, una
    -- noticia bancaria en un entorno de tasas altas se lee igual que en uno de
    -- tasas bajas, que es justo lo que el PRD §3.5 quiere evitar.
    macro_context       JSONB,

    correlated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT gold_corr_unico UNIQUE (news_guid, ticker, price_date),
    -- Coherencia del proxy: o es proxy y tiene ticker sustituto, o no lo es y
    -- no lo tiene. Sin esto se cuelan filas con is_proxy=true y proxy_ticker
    -- NULL, que rompen cualquier análisis de impacto indirecto.
    CONSTRAINT gold_corr_proxy_coherente CHECK (
        (is_proxy AND proxy_ticker IS NOT NULL)
        OR (NOT is_proxy AND proxy_ticker IS NULL)
    ),
    -- El precio nunca puede medirse antes de la noticia.
    CONSTRAINT gold_corr_precio_posterior CHECK (price_date >= news_date)
);

CREATE INDEX IF NOT EXISTS idx_gold_corr_ticker      ON gold_news_market_corr (ticker, news_date DESC);
CREATE INDEX IF NOT EXISTS idx_gold_corr_proxy       ON gold_news_market_corr (news_date DESC) WHERE is_proxy;
CREATE INDEX IF NOT EXISTS idx_gold_corr_news        ON gold_news_market_corr (news_guid);
