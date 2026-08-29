-- Registro de gasto del brief ejecutivo (F4, 28-ago-2026).
--
-- El techo de $20/mes del workspace de Anthropic es la red de seguridad, no el
-- control primario: es mensual y corta hasta el día 1, así que un bucle que lo
-- agote el día 3 deja al comité sin brief tres semanas. `MAX_LLAMADAS_POR_
-- CORRIDA` ya protege contra el bucle, pero no contra la DERIVA — si el
-- contexto crece y el brief pasa de $0,08 a $0,40, nadie se entera hasta que
-- el workspace corta a mitad de mes.
--
-- Esta tabla es lo que permite verlo venir: una fila por corrida con su coste,
-- y el módulo consulta el acumulado del mes ANTES de llamar.
--
-- El coste se guarda a precio de LISTA, calculado en el cliente. No es la
-- factura real (puede haber descuentos negociados) y no pretende serlo: sirve
-- para detectar una tendencia, no para contabilidad.

CREATE TABLE IF NOT EXISTS gold_brief_ejecuciones (
    id              SERIAL PRIMARY KEY,
    fecha_cierre    DATE        NOT NULL,
    modelo          TEXT        NOT NULL,
    sectores        TEXT[]      NOT NULL,
    n_noticias      INTEGER     NOT NULL,
    tokens_entrada  INTEGER     NOT NULL,
    tokens_salida   INTEGER     NOT NULL,
    usd_lista       NUMERIC(10, 4) NOT NULL,
    ejecutado_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_brief_ejecuciones_mes
    ON gold_brief_ejecuciones (ejecutado_at DESC);

COMMENT ON TABLE gold_brief_ejecuciones IS
    'Una fila por corrida del brief. Permite ver la deriva de coste antes de '
    'que el techo del workspace corte.';
COMMENT ON COLUMN gold_brief_ejecuciones.usd_lista IS
    'Coste a precio de lista calculado en el cliente. No es la factura real; '
    'sirve para detectar tendencia, no para contabilidad.';
