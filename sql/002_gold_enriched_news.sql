-- Capa Gold — noticias enriquecidas por el LLM (PRD §5.3).
--
-- La escribe `src.pipeline.enrich`. Los campos de embedding quedan NULL en esta
-- etapa: la vectorización pertenece a `index` (PRD §4.4 paso 7), y separarlas
-- permite reindexar con otro modelo sin repetir la inferencia del LLM, que es
-- la parte cara.

CREATE TABLE IF NOT EXISTS gold_enriched_news (
    id              SERIAL      PRIMARY KEY,
    -- UNIQUE, no solo FK: la clave natural sigue siendo el guid de Silver, y
    -- es lo que hace idempotente el reproceso del enriquecimiento.
    guid            TEXT        NOT NULL UNIQUE REFERENCES silver_news(guid) ON DELETE CASCADE,

    -- --- Heredados de Silver -------------------------------------------------
    -- Se copian en vez de consultarse por JOIN: gold_enriched_news es la tabla
    -- que alimenta la búsqueda semántica, y una consulta Top-K no debería
    -- necesitar tocar Silver para mostrar un titular.
    source          TEXT        NOT NULL,
    title           TEXT        NOT NULL,
    content         TEXT        NOT NULL,
    url             TEXT        NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    ingested_at     TIMESTAMPTZ NOT NULL,

    -- --- Embedding (lo rellena `index`, no `enrich`) -------------------------
    embedding       vector(1024),

    -- --- NER -----------------------------------------------------------------
    ner_tickers     TEXT[],
    ner_persons     TEXT[],
    ner_orgs        TEXT[],
    ner_sectors     TEXT[],

    -- --- Sentimiento ---------------------------------------------------------
    sentiment_score DOUBLE PRECISION,
    sentiment_label TEXT,

    -- --- Detección M&A -------------------------------------------------------
    is_ma_event     BOOLEAN     NOT NULL DEFAULT FALSE,
    ma_event_type   TEXT        NOT NULL DEFAULT 'none',
    ma_confidence   DOUBLE PRECISION,

    -- --- Etiquetado Fintech --------------------------------------------------
    fintech_flag                BOOLEAN NOT NULL DEFAULT FALSE,
    fintechs_identified         TEXT[],
    traditional_banks_mentioned TEXT[],
    -- Sector inferido para resolver el proxy ticker aguas abajo (PRD §3.3).
    -- Vive aquí y no en gold_news_market_corr porque es una salida del LLM;
    -- `correlate` solo lo traduce a ticker con la tabla de mapeo.
    sector_affected             TEXT,

    -- --- Trazabilidad --------------------------------------------------------
    enriched_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Qué modelo produjo esto. Sin esta columna, comparar resultados entre
    -- modelos o detectar una regresión tras cambiar de modelo es imposible.
    model_version   TEXT        NOT NULL,

    CONSTRAINT gold_news_sentiment_label CHECK (
        sentiment_label IS NULL
        OR sentiment_label IN ('positive', 'negative', 'neutral')
    ),
    CONSTRAINT gold_news_sentiment_score CHECK (
        sentiment_score IS NULL OR sentiment_score BETWEEN 0.0 AND 1.0
    ),
    CONSTRAINT gold_news_ma_event_type CHECK (
        ma_event_type IN ('acquisition', 'merger', 'partnership', 'none')
    ),
    CONSTRAINT gold_news_ma_confidence CHECK (
        ma_confidence IS NULL OR ma_confidence BETWEEN 0.0 AND 1.0
    ),
    -- Coherencia: si no es evento M&A, el tipo tiene que ser 'none'. Sin esto
    -- se cuelan filas con is_ma_event=false y ma_event_type='merger', que
    -- hacen que dos consultas igualmente razonables den cuentas distintas.
    CONSTRAINT gold_news_ma_coherente CHECK (
        is_ma_event OR ma_event_type = 'none'
    )
);

-- Criterio del PRD §8: "detección M&A con al menos 1 caso positivo". Índice
-- parcial porque los eventos M&A son la minoría y son lo que se consulta.
CREATE INDEX IF NOT EXISTS idx_gold_news_ma
    ON gold_enriched_news (published_at DESC) WHERE is_ma_event;

CREATE INDEX IF NOT EXISTS idx_gold_news_fintech
    ON gold_enriched_news (published_at DESC) WHERE fintech_flag;

CREATE INDEX IF NOT EXISTS idx_gold_news_tickers
    ON gold_enriched_news USING GIN (ner_tickers);

CREATE INDEX IF NOT EXISTS idx_gold_news_sentiment
    ON gold_enriched_news (sentiment_label, published_at DESC);

-- Pendiente de `index`: el índice vectorial (ivfflat/hnsw) se crea cuando haya
-- embeddings, no ahora — construirlo sobre una tabla vacía no sirve de nada y
-- ivfflat necesita datos para entrenar sus listas.
