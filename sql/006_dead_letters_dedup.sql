-- Diagnóstico del 14-ago-2026: `silver_dead_letters` no deduplicaba (a
-- diferencia de `silver_news`, que sí hace UPSERT) y algunas fuentes reenvían
-- casi el mismo lote en cada ingesta. Ejemplo real sobre la ventana 10-14 ago:
--
--   fuente        filas    guids distintos   veces por guid
--   bloomberg     4 646    89                52x
--   financiero    10 790   822               13x
--   google_news   2 881    361               8x
--
-- Bloomberg en particular parece devolver una lista casi estática (~89
-- artículos "más leídos"): cada corrida los rechaza otra vez por
-- MISSING_ENTITY y los volvía a insertar como fila nueva. La tabla crecía sin
-- aportar información.
--
-- El diseño original (ver comentario histórico en `cargar_dead_letters`)
-- quería precisamente esa señal: saber que algo lleva N días rechazándose.
-- Este cambio la conserva, pero como contador (`times_rejected`) en vez de
-- como filas repetidas — mismo dato, sin el ruido.
--
-- Un registro sin guid no tiene clave natural para agregar y sigue
-- insertándose como fila nueva cada vez (comportamiento previo, sin cambios).

ALTER TABLE silver_dead_letters
    ADD COLUMN IF NOT EXISTS first_rejected_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS times_rejected INT NOT NULL DEFAULT 1;

COMMENT ON COLUMN silver_dead_letters.rejected_at IS
    'Última vez que se rechazó este (source, guid). Antes de la deduplicación (2026-08-14) era el momento de un evento individual.';
COMMENT ON COLUMN silver_dead_letters.first_rejected_at IS
    'Primera vez que se vio este rechazo. Junto con rejected_at delimita cuánto lleva persistiendo.';
COMMENT ON COLUMN silver_dead_letters.times_rejected IS
    'Cuántas corridas han rechazado este mismo (source, guid). Sustituye al COUNT(*) por GUID que antes exigía filas repetidas.';

-- Backfill de lo ya existente: "primera vez" = su propio rejected_at.
UPDATE silver_dead_letters SET first_rejected_at = rejected_at WHERE first_rejected_at IS NULL;

ALTER TABLE silver_dead_letters ALTER COLUMN first_rejected_at SET NOT NULL;
ALTER TABLE silver_dead_letters ALTER COLUMN first_rejected_at SET DEFAULT NOW();

-- Colapsa los duplicados acumulados antes de este cambio, para poder crear el
-- índice único. Se conserva el motivo/detalle/payload de la fila MÁS
-- RECIENTE de cada (source, guid) — refleja el estado actual del rechazo, no
-- el primero que se vio.
DO $$
DECLARE
    total_colapsadas BIGINT;
BEGIN
    WITH agregados AS (
        SELECT source, guid,
               MIN(id) AS id_representante,
               MIN(rejected_at) AS primero,
               MAX(rejected_at) AS ultimo,
               COUNT(*) AS veces
        FROM silver_dead_letters
        WHERE guid IS NOT NULL
        GROUP BY source, guid
        HAVING COUNT(*) > 1
    ),
    ultimo_por_guid AS (
        SELECT DISTINCT ON (dl.source, dl.guid)
               dl.source, dl.guid, dl.rejection_reason, dl.rejection_detail,
               dl.raw_payload, dl.batch_uuid
        FROM silver_dead_letters dl
        JOIN agregados a ON a.source = dl.source AND a.guid = dl.guid
        ORDER BY dl.source, dl.guid, dl.rejected_at DESC
    ),
    actualizados AS (
        UPDATE silver_dead_letters dl
        SET first_rejected_at = a.primero,
            rejected_at        = a.ultimo,
            times_rejected      = a.veces,
            rejection_reason    = u.rejection_reason,
            rejection_detail    = u.rejection_detail,
            raw_payload         = u.raw_payload,
            batch_uuid          = u.batch_uuid
        FROM agregados a
        JOIN ultimo_por_guid u ON u.source = a.source AND u.guid = a.guid
        WHERE dl.id = a.id_representante
        RETURNING dl.id
    ),
    eliminados AS (
        DELETE FROM silver_dead_letters dl
        USING agregados a
        WHERE dl.source = a.source
          AND dl.guid = a.guid
          AND dl.id <> a.id_representante
        RETURNING dl.id
    )
    SELECT COUNT(*) INTO total_colapsadas FROM eliminados;

    RAISE NOTICE 'silver_dead_letters: % filas duplicadas colapsadas', total_colapsadas;
END $$;

-- A partir de aquí, un (source, guid) repetido actualiza en vez de insertar.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dead_letters_source_guid
    ON silver_dead_letters (source, guid)
    WHERE guid IS NOT NULL;
