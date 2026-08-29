-- ROE en gold_valuation (29-ago-2026, F1 profundidad).
--
-- El ROE es la contraparte del P/VL: un banco cotiza sobre su valor en libros
-- en la medida en que gana sobre ese capital, así que los dos números solo
-- dicen algo puestos uno al lado del otro. Por eso vive aquí y no en una tabla
-- de ratios aparte.
--
-- DEFINICIÓN, validada contra la cifra que publica la propia emisora. Banorte
-- declaró 23.4% de ROE del Grupo en 1T25; de las tres formas usuales, la
-- anualización del trimestre sobre el capital de cierre es la que reproduce
-- ese número:
--
--     utilidad_neta_trimestral × 4 / capital_contable   →  22.9%   ← esta
--     TTM sobre capital de cierre                       →  21.2%
--     TTM sobre capital promedio del año                →  22.2%
--
-- Se prefiere la anualizada también porque no arrastra cuatro trimestres: un
-- solo periodo defectuoso envenenaba el TTM durante un año entero, que es
-- justo lo que pasaba con el 2025-06-30 de Yahoo antes de mandarlo a
-- cuarentena (ver el `ge=0` de ingresos_totales en el contrato).
--
-- NO se convierte por tipo de cambio, a diferencia del P/U y el P/VL: es un
-- cociente entre dos cantidades del MISMO estado financiero y en la misma
-- moneda, así que el factor se cancela. Aplicarlo sería un error silencioso.
--
-- Sin CHECK de positividad: una emisora puede perder dinero, y un ROE negativo
-- es un dato legítimo que la tabla debe poder representar.

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS roe_anualizado      DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS roe_source          TEXT,
    ADD COLUMN IF NOT EXISTS roe_mediana_sector  DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS roe_vs_sector_pp    DOUBLE PRECISION;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'gold_valuation_roe_source'
    ) THEN
        ALTER TABLE gold_valuation
            ADD CONSTRAINT gold_valuation_roe_source
            CHECK (roe_source IS NULL OR roe_source IN ('trimestral', 'anual'));
    END IF;
END $$;

COMMENT ON COLUMN gold_valuation.roe_anualizado IS
    'ROE en porcentaje. Trimestral: utilidad_neta × 4 / capital_contable. '
    'Anual: utilidad_neta / capital_contable. Sin conversión FX — el cociente '
    'la cancela.';
COMMENT ON COLUMN gold_valuation.roe_vs_sector_pp IS
    'Diferencia contra la mediana del sector en PUNTOS PORCENTUALES, no en %: '
    'el premium relativo de un porcentaje se malinterpreta con facilidad.';
