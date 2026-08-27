-- Comparación sectorial de múltiplos en gold_valuation (26-ago-2026).
--
-- Hasta ahora el z-score era solo contra la propia historia de cada emisora:
-- respondía "¿está cara frente a como ha cotizado ella misma?". Falta la otra
-- mitad de la pregunta, que es la que usa un analista: "¿está cara frente a
-- sus pares?". Una emisora puede estar barata contra su historia y aun así ser
-- la más cara de su sector, y esas dos lecturas llevan a decisiones opuestas.
--
-- Por qué MEDIANA y PREMIO, y no un z-score sectorial:
-- los sectores aquí tienen 3 a 6 emisoras. Con esa n, una desviación estándar
-- es inestable y un solo atípico la domina — CEMEXCPO llega a 71x de P/U y
-- reventaría la media de `materiales`. La mediana es robusta a eso, y el premio
-- porcentual sobre ella («cotiza 15% por debajo de la mediana de su sector») se
-- lee sin traducir nada. El rank complementa: dice la posición ordinal, que no
-- depende de la forma de la distribución.
--
-- n_sector viaja en cada fila a propósito: un premio calculado sobre 3
-- emisoras y otro sobre 6 no merecen la misma confianza, y quien consulte la
-- tabla debe poder distinguirlos sin ir al código.

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS sector TEXT;

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS n_sector INTEGER;

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pe_mediana_sector DOUBLE PRECISION;
ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pe_premium_sector_pct DOUBLE PRECISION;
ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pe_rank_sector INTEGER;

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pb_mediana_sector DOUBLE PRECISION;
ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pb_premium_sector_pct DOUBLE PRECISION;
ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS pb_rank_sector INTEGER;

CREATE INDEX IF NOT EXISTS idx_valuation_sector
    ON gold_valuation (sector, date DESC);

COMMENT ON COLUMN gold_valuation.n_sector IS
    'Emisoras del sector con múltiplo esa fecha. NULL en las comparaciones no '
    'publicadas por no alcanzar MIN_EMISORAS_SECTOR.';
COMMENT ON COLUMN gold_valuation.pe_premium_sector_pct IS
    'Premio (+) o descuento (-) porcentual del P/U frente a la mediana de su sector.';
COMMENT ON COLUMN gold_valuation.pe_rank_sector IS
    'Posición del P/U dentro del sector esa fecha; 1 = el más barato.';
