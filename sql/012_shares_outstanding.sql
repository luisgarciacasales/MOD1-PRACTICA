-- F2 (25-ago-2026) — acciones en circulación, pieza que faltaba para P/VL
-- (book_value_per_share = capital_contable / acciones_en_circulacion).
-- Mismo campo del mismo balance ya ingerido ("Ordinary Shares Number"), así
-- que se agrega como columna a las cuatro tablas de fundamentales existentes
-- en vez de crear una fuente o tabla nueva. Ver src/contracts/fundamentals.py
-- y src/config/fundamentales.py (CAMPOS_BALANCE) para el mapeo.

ALTER TABLE silver_fundamentals
    ADD COLUMN IF NOT EXISTS acciones_en_circulacion DOUBLE PRECISION;
ALTER TABLE silver_fundamentals
    ADD CONSTRAINT silver_fundamentals_acciones_positivo
    CHECK (acciones_en_circulacion IS NULL OR acciones_en_circulacion > 0);

ALTER TABLE silver_fundamentals_anual
    ADD COLUMN IF NOT EXISTS acciones_en_circulacion DOUBLE PRECISION;
ALTER TABLE silver_fundamentals_anual
    ADD CONSTRAINT silver_fundamentals_anual_acciones_positivo
    CHECK (acciones_en_circulacion IS NULL OR acciones_en_circulacion > 0);

ALTER TABLE gold_fundamentals
    ADD COLUMN IF NOT EXISTS acciones_en_circulacion DOUBLE PRECISION;

ALTER TABLE gold_fundamentals_anual
    ADD COLUMN IF NOT EXISTS acciones_en_circulacion DOUBLE PRECISION;
