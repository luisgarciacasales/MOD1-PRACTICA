-- Trazabilidad de la conversión de moneda en gold_valuation (26-ago-2026).
--
-- BBVA.MX y SANN.MX reportan en EUR; CEMEXCPO.MX y GMEXICOB.MX en USD; las
-- cuatro cotizan en MXN. Hasta ahora se excluían de la valuación
-- (TICKERS_MONEDA_FINANCIERA_DISTINTA) porque el P/U salía en cientos de
-- veces: un artefacto de conversión, no una lectura. Eso costaba 4 de 16
-- emisoras, dos de ellas bancos, justo el sector que el roadmap prioriza.
--
-- Ahora se convierten con el tipo de cambio del SIE. Estas columnas declaran
-- QUÉ se hizo con cada fila, por el mismo criterio que eps_source/book_source:
-- un múltiplo convertido y uno nativo no son la misma medida, y quien lea la
-- tabla debe poder distinguirlos sin recurrir al código.
--
-- moneda_reporte = 'MXN' significa que no hubo conversión (fx_aplicado NULL).

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS moneda_reporte TEXT NOT NULL DEFAULT 'MXN';

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS fx_aplicado DOUBLE PRECISION;

COMMENT ON COLUMN gold_valuation.moneda_reporte IS
    'Moneda de los estados financieros de origen: MXN (sin conversión), EUR o USD.';
COMMENT ON COLUMN gold_valuation.fx_aplicado IS
    'Pesos por unidad de moneda extranjera usados para convertir la UPA más '
    'reciente. NULL cuando moneda_reporte = MXN.';
