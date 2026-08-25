-- F2 (25-ago-2026) — P/VL (precio/valor en libros) junto al P/U ya
-- desplegado. book_value_per_share = capital_contable / acciones_en_
-- circulacion (ver 012_shares_outstanding.sql). A diferencia de la UPA, el
-- valor en libros por acción NO se acumula en TTM — es una foto de balance,
-- no un flujo — así que solo usa el trimestre (o ejercicio) más reciente
-- disponible a esa fecha, mismo rezago de 45 días que el P/U.
--
-- book_source espeja a eps_source por la misma razón: trimestral y anual no
-- son la misma cadencia de actualización, y declarar cuál se usó es más
-- honesto que promediarlos en silencio.

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS book_value_per_share DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pb_ratio              DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS pb_zscore_1y           DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS book_source            TEXT;

ALTER TABLE gold_valuation
    ADD CONSTRAINT gold_valuation_book_positivo
    CHECK (book_value_per_share IS NULL OR book_value_per_share > 0);
ALTER TABLE gold_valuation
    ADD CONSTRAINT gold_valuation_pb_positivo
    CHECK (pb_ratio IS NULL OR pb_ratio > 0);
ALTER TABLE gold_valuation
    ADD CONSTRAINT gold_valuation_book_source
    CHECK (book_source IS NULL OR book_source IN ('trimestral', 'anual'));
