-- Resultados del backtest de señales (F3, 27-ago-2026).
--
-- Por qué existe esta tabla y no solo un reporte a consola: una validación que
-- no queda escrita se vuelve a discutir cada vez. Guardar el resultado con su
-- n y su fecha de cálculo permite comparar la misma señal dentro de seis meses,
-- cuando haya más muestra, y ver si aguantó.
--
-- `n_efectiva` no es adorno. Los retornos a 20 días medidos todos los días se
-- solapan 19/20, así que las observaciones NO son independientes y cualquier
-- prueba de significancia sobre `n` estaría inflada. n_efectiva = n / horizonte
-- es la corrección burda pero honesta.
--
-- Terciles y no quintiles: con 16 emisoras un quintil son tres nombres, y el
-- resultado lo dominaría cualquiera de ellos.

CREATE TABLE IF NOT EXISTS gold_backtest_senal (
    id              SERIAL PRIMARY KEY,
    senal           TEXT        NOT NULL,
    horizonte_dias  INTEGER     NOT NULL,
    tercil          INTEGER     NOT NULL,
    n               INTEGER     NOT NULL,
    n_efectiva      INTEGER     NOT NULL,
    exceso_medio    DOUBLE PRECISION,
    exceso_mediano  DOUBLE PRECISION,
    desv_exceso     DOUBLE PRECISION,
    senal_media     DOUBLE PRECISION,
    desde           DATE        NOT NULL,
    hasta           DATE        NOT NULL,
    calculado_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT gold_backtest_unico UNIQUE (senal, horizonte_dias, tercil)
);

COMMENT ON TABLE gold_backtest_senal IS
    'F3: ¿predice la señal el exceso de retorno futuro? Una fila por '
    '(señal, horizonte, tercil). El tercil 1 es el extremo BARATO.';
COMMENT ON COLUMN gold_backtest_senal.n_efectiva IS
    'n / horizonte. Los retornos solapados no son observaciones independientes; '
    'usar n cruda para significancia la infla.';
COMMENT ON COLUMN gold_backtest_senal.exceso_medio IS
    'Exceso de retorno sobre ^MXX en el horizonte, en %. Sobre retorno bruto '
    'mediría beta de mercado, no la señal.';
