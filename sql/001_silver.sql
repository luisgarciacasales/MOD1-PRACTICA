-- Capa Silver — esquema relacional (PRD §5.2).
--
-- Espeja los contratos Pydantic de src/contracts/. Duplicar las reglas en la
-- base es deliberado: Pydantic protege la ruta del pipeline, los CHECK protegen
-- la tabla de cualquier otra escritura (un psql manual, un script futuro, un
-- reproceso a medio hacer). Si se cambia un contrato, se cambian los dos.
--
-- Todo es IF NOT EXISTS: este archivo debe poder reaplicarse sin efecto.

-- ---------------------------------------------------------------------------
-- silver_news — noticias validadas
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_news (
    -- Clave natural, no serial: SHA-256 de source + url + published_at.
    -- Es lo que hace idempotente la carga (PRD §6.3).
    guid            TEXT        PRIMARY KEY,
    source          TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tickers         TEXT[],
    sector          TEXT,
    entities        TEXT[],
    enriched        BOOLEAN     NOT NULL DEFAULT FALSE,
    macro_bypass    BOOLEAN     NOT NULL DEFAULT FALSE,
    raw_batch_uuid  UUID        NOT NULL,

    CONSTRAINT silver_news_guid_sha256
        CHECK (guid ~ '^[0-9a-f]{64}$'),
    CONSTRAINT silver_news_source_valida
        CHECK (source IN ('bmv_eventos', 'financiero', 'economista', 'bloomberg')),
    CONSTRAINT silver_news_title_longitud
        CHECK (char_length(title) BETWEEN 1 AND 1024),
    CONSTRAINT silver_news_content_longitud
        CHECK (char_length(content) BETWEEN 1 AND 8192),

    -- Integridad semántica (PRD §6.2): al menos un Ticker, Sector o Entidad,
    -- salvo que aplique el bypass macroeconómico. Es la regla de negocio
    -- central del contrato y por eso vive también aquí.
    CONSTRAINT silver_news_integridad_semantica CHECK (
        macro_bypass
        OR (tickers  IS NOT NULL AND array_length(tickers, 1)  > 0)
        OR (sector   IS NOT NULL AND char_length(sector)       > 0)
        OR (entities IS NOT NULL AND array_length(entities, 1) > 0)
    )
);

-- La etapa de enriquecimiento consulta "dame lo que aún no procesé". Índice
-- parcial: solo indexa las filas pendientes, que son las pocas que se buscan.
CREATE INDEX IF NOT EXISTS idx_silver_news_pendientes
    ON silver_news (published_at DESC) WHERE NOT enriched;

CREATE INDEX IF NOT EXISTS idx_silver_news_published_at
    ON silver_news (published_at DESC);

-- GIN sobre el array de tickers: soporta el filtro `tickers @> ARRAY['GFNORTE']`
-- que usará la correlación noticias↔mercado.
CREATE INDEX IF NOT EXISTS idx_silver_news_tickers
    ON silver_news USING GIN (tickers);

CREATE INDEX IF NOT EXISTS idx_silver_news_batch
    ON silver_news (raw_batch_uuid);

-- ---------------------------------------------------------------------------
-- silver_dead_letters — cuarentena
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_dead_letters (
    id                SERIAL      PRIMARY KEY,
    -- Nullable: un registro puede venir tan roto que no permita calcular guid.
    guid              TEXT,
    source            TEXT        NOT NULL,
    -- El payload original completo. Sin esto, un rechazo es una estadística en
    -- lugar de algo diagnosticable y reprocesable.
    raw_payload       JSONB       NOT NULL,
    rejection_reason  TEXT        NOT NULL,
    rejection_detail  TEXT,
    rejected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    batch_uuid        UUID        NOT NULL,

    -- Espeja el enum RejectionReason de src/contracts/rejections.py. Motivos
    -- tipados, no strings ad hoc: es lo que hace de
    -- `GROUP BY rejection_reason` una métrica de calidad utilizable.
    CONSTRAINT silver_dead_letters_motivo_valido CHECK (
        rejection_reason IN (
            'MISSING_ENTITY', 'TYPE_MISMATCH', 'INVALID_URL', 'INVALID_DATE',
            'MISSING_FIELD', 'OUT_OF_RANGE', 'DUPLICATE_KEY', 'UNKNOWN_SOURCE'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_dead_letters_motivo
    ON silver_dead_letters (rejection_reason, rejected_at DESC);

CREATE INDEX IF NOT EXISTS idx_dead_letters_batch
    ON silver_dead_letters (batch_uuid);

-- ---------------------------------------------------------------------------
-- silver_market_prices — OHLCV diario
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_market_prices (
    id              SERIAL      PRIMARY KEY,
    ticker          TEXT        NOT NULL,
    date            DATE        NOT NULL,
    open            DOUBLE PRECISION NOT NULL,
    high            DOUBLE PRECISION NOT NULL,
    low             DOUBLE PRECISION NOT NULL,
    close           DOUBLE PRECISION NOT NULL,
    adj_close       DOUBLE PRECISION NOT NULL,
    volume          BIGINT      NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_batch_uuid  UUID        NOT NULL,

    -- Clave compuesta: el objetivo de idempotencia del PRD §5.2.
    CONSTRAINT silver_market_prices_unico UNIQUE (ticker, date),

    CONSTRAINT silver_market_prices_positivos CHECK (
        open > 0 AND high > 0 AND low > 0 AND close > 0 AND adj_close > 0
    ),
    CONSTRAINT silver_market_prices_volumen CHECK (volume >= 0),
    -- Coherencia OHLC: un registro con low > high pasa "precio > 0" y aun así
    -- corrompería los retornos y la volatilidad calculados en Gold.
    CONSTRAINT silver_market_prices_rango_ohlc CHECK (
        low <= high AND open BETWEEN low AND high AND close BETWEEN low AND high
    )
);

CREATE INDEX IF NOT EXISTS idx_market_prices_ticker_fecha
    ON silver_market_prices (ticker, date DESC);

-- ---------------------------------------------------------------------------
-- silver_macro_indicators — series del SIE de BANXICO
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_macro_indicators (
    id              SERIAL      PRIMARY KEY,
    series_id       TEXT        NOT NULL,
    date            DATE        NOT NULL,
    -- Sin CHECK de signo: hay series legítimamente negativas (variación
    -- intermensual del INPC, por ejemplo).
    value           DOUBLE PRECISION NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    raw_batch_uuid  UUID        NOT NULL,

    CONSTRAINT silver_macro_indicators_unico UNIQUE (series_id, date)
);

CREATE INDEX IF NOT EXISTS idx_macro_serie_fecha
    ON silver_macro_indicators (series_id, date DESC);

-- ---------------------------------------------------------------------------
-- silver_fintech_dict — diccionario maestro Finnovista
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver_fintech_dict (
    id               SERIAL      PRIMARY KEY,
    legal_name       TEXT        NOT NULL,
    -- Clave natural del diccionario: es el nombre que aparece en las noticias.
    commercial_name  TEXT        NOT NULL,
    -- NULL = no cotiza en BMV = sus noticias necesitan proxy ticker (PRD §3.3).
    ticker           TEXT,
    sector           TEXT        NOT NULL,
    country          TEXT        NOT NULL DEFAULT 'MX',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT silver_fintech_dict_unico UNIQUE (commercial_name, country)
);

-- Índice trigram para el cross-reference difuso: las noticias escriben "Nu",
-- "Nubank" y "Nu México" para la misma entidad.
CREATE INDEX IF NOT EXISTS idx_fintech_nombre_trgm
    ON silver_fintech_dict USING GIN (commercial_name gin_trgm_ops);
