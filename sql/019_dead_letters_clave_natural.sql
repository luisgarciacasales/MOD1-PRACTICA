-- Deduplicación de la cuarentena para mercado y macro (27-ago-2026).
--
-- La migración 006 (14-ago) hizo que un rechazo repetido contara como
-- `times_rejected` en vez de como fila nueva, pero solo alcanzó a los
-- registros que YA traían `guid` — las noticias. Mercado y macro nunca lo
-- tuvieron, así que el índice único (parcial, `WHERE guid IS NOT NULL`) no los
-- cubría y cada pasada los reinsertaba.
--
-- Con el lote diario apenas se notaba. El refresco histórico semanal
-- introducido el 26-ago lo convierte en un problema real: cada semana vuelve a
-- rechazar los MISMOS 'N/E' de Banxico y los mismos días con OHLC incoherente
-- de Yahoo. Medido antes de este cambio, sobre 10 090 filas de cuarentena:
--
--   fuente          filas   con guid   rechazos acumulados
--   financiero      1 476   1 476      60 064   <- deduplica bien
--   google_news       645     645      26 210   <- deduplica bien
--   banxico         3 810       0       3 810   <- una fila por rechazo
--   yahoo_finance   4 001       0       4 001   <- una fila por rechazo
--
-- La clave natural es la misma que usa Silver: (ticker, date) en precios,
-- (series_id, fecha) en Banxico, (indicador_id, periodo) en INEGI. Se compone
-- del payload CRUDO, no del contrato, porque el rechazo ocurre justamente
-- cuando el registro no llegó a normalizarse. Ver `guid_natural` en
-- src/contracts/rejections.py — este backfill replica esa misma lógica en SQL.
--
-- Una fila cuyo payload no tenga sus campos de clave se queda con guid NULL y
-- sigue insertándose cada vez: es el comportamiento previo, y es preferible a
-- inventar una clave que colapse rechazos distintos en uno.

-- 1. Soltar el índice único ANTES de poblar el guid. Sin esto el UPDATE falla
--    a mitad: poblar la clave de 4.001 filas de yahoo_finance crea los
--    duplicados que el paso 3 va a colapsar, pero el índice los rechaza en el
--    instante en que aparecen. El orden correcto es soltar, poblar, colapsar y
--    volver a crear — la 006 no tuvo que hacerlo porque entonces el índice aún
--    no existía.
DROP INDEX IF EXISTS uq_dead_letters_source_guid;

-- 2. Backfill del guid en lo ya acumulado.
UPDATE silver_dead_letters SET guid =
    CASE source
        WHEN 'yahoo_finance' THEN
            (raw_payload->>'ticker') || ':' || (raw_payload->>'date')
        WHEN 'yahoo_fundamentals' THEN
            (raw_payload->>'ticker') || ':' || (raw_payload->>'period_end')
        WHEN 'yahoo_fundamentals_anual' THEN
            (raw_payload->>'ticker') || ':' || (raw_payload->>'period_end')
        WHEN 'banxico' THEN
            (raw_payload->>'series_id') || ':' || (raw_payload->>'fecha')
        WHEN 'inegi' THEN
            (raw_payload->>'indicador_id') || ':' || (raw_payload->>'periodo')
    END
WHERE guid IS NULL
  AND source IN ('yahoo_finance', 'yahoo_fundamentals',
                 'yahoo_fundamentals_anual', 'banxico', 'inegi');

-- El CASE devuelve NULL si falta cualquiera de los dos campos (concatenación
-- con NULL en SQL), así que las filas sin clave componible se quedan como
-- estaban. No hace falta filtrarlas aparte.

-- 3. Colapsar los duplicados que el backfill acaba de hacer visibles. Mismo
--    procedimiento que la 006: se conserva el motivo/detalle/payload de la
--    fila MÁS RECIENTE de cada (source, guid), y el contador acumula cuántas
--    veces se había rechazado.
DO $$
DECLARE
    total_colapsadas BIGINT;
BEGIN
    WITH agregados AS (
        SELECT source, guid,
               MIN(id) AS id_representante,
               MIN(first_rejected_at) AS primero,
               MAX(rejected_at) AS ultimo,
               SUM(times_rejected) AS veces
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
            rejected_at       = a.ultimo,
            times_rejected    = a.veces,
            rejection_reason  = u.rejection_reason,
            rejection_detail  = u.rejection_detail,
            raw_payload       = u.raw_payload,
            batch_uuid        = u.batch_uuid
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

    RAISE NOTICE 'silver_dead_letters: % filas de mercado/macro colapsadas', total_colapsadas;
END $$;

-- 4. Restaurar el índice. A partir de aquí un (source, guid) repetido
--    actualiza y suma times_rejected, también en mercado y macro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_dead_letters_source_guid
    ON silver_dead_letters (source, guid)
    WHERE guid IS NOT NULL;
