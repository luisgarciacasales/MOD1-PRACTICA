-- Métricas relativas al benchmark del mercado (S&P/BMV IPC, `^MXX`).
--
-- Sin referencia no se puede distinguir «Banorte subió 3%» de «Banorte superó
-- al mercado en 1,8%». Para un motor de analítica de desempeño esa distinción
-- es el vocabulario básico: un retorno absoluto mezcla el movimiento propio de
-- la institución con el del mercado entero, y solo el segundo se explica por
-- factores macro comunes a todas.

ALTER TABLE gold_market_prices
    -- Retorno del índice ese mismo día. Se materializa en cada fila en lugar de
    -- resolverse por JOIN en cada consulta: son 500 filas por emisora y el JOIN
    -- se repetiría en todos los análisis.
    ADD COLUMN IF NOT EXISTS benchmark_return_pct DOUBLE PRECISION,

    -- Exceso de retorno: cuánto se movió la emisora por encima del mercado.
    -- Es la medida más directa de desempeño relativo.
    ADD COLUMN IF NOT EXISTS excess_return_pct DOUBLE PRECISION,

    -- Beta móvil a 60 sesiones = covarianza(emisora, índice) / varianza(índice).
    -- Mide sensibilidad al mercado: beta > 1 amplifica sus movimientos, beta < 1
    -- los amortigua. Se calcula sobre 60 sesiones (~3 meses) porque una ventana
    -- más corta es ruido y una más larga tarda demasiado en reflejar un cambio
    -- en el perfil de riesgo de la institución.
    ADD COLUMN IF NOT EXISTS beta_60d DOUBLE PRECISION,

    -- Correlación móvil a 60 sesiones. Complementa a la beta: dos emisoras
    -- pueden tener la misma beta con correlaciones muy distintas, y en ese caso
    -- la beta es mucho menos fiable como predictor.
    ADD COLUMN IF NOT EXISTS correlacion_60d DOUBLE PRECISION;

-- Consultas típicas: «qué instituciones superaron al mercado» y «cuáles son las
-- más sensibles». Ambas ordenan por estas columnas sobre la fecha más reciente.
CREATE INDEX IF NOT EXISTS idx_gold_prices_exceso
    ON gold_market_prices (date DESC, excess_return_pct DESC)
    WHERE excess_return_pct IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_gold_prices_beta
    ON gold_market_prices (date DESC, beta_60d DESC)
    WHERE beta_60d IS NOT NULL;

COMMENT ON COLUMN gold_market_prices.beta_60d IS
    'Beta movil a 60 sesiones frente al S&P/BMV IPC. NULL en la fila del propio '
    'indice: su beta contra si mismo es trivialmente 1 y solo distorsionaria '
    'cualquier agregacion.';
