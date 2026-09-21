-- La clave natural de una noticia no identifica al artículo (21-sep-2026).
--
-- `calcular_guid` es SHA-256 de (source, url, published_at) y su docstring
-- promete que "el mismo artículo reingerido dos veces produce el mismo guid".
-- La promesa descansa en que la URL sea estable, y no lo es. Dos fuentes lo
-- rompen por caminos distintos:
--
--   google_news — sirve el artículo como blob de redirección protobuf. El
--   mismo hecho vuelve con el id primario y el alterno intercambiados:
--     …CBMia0FVX3lxTE9FZ3Jh…0gFrQVVfeXFMUHJSdXhwU1lt…
--     …CBMia0FVX3lxTFByUnV4cFNZbTVRRXVX…0gFrQVVfeXFMUHJSdXhwU1lt…
--   El primer segmento del segundo ES el segundo segmento del primero.
--
--   eleconomista_archivo — el medio corrigió su propio slug:
--     …reclamo-del-SAT-20200217-0037.html
--     …reclamo-de-SAT-20200217-0037.html
--
-- En ambos casos `source`, `title` y `published_at` coinciden al segundo y
-- solo cambia la URL, así que `ON CONFLICT (guid)` no tiene nada que colapsar
-- y el artículo entra dos veces. Medido antes de este cambio:
--
--   fuente                 hechos   filas   sobrantes
--   google_news                26      52          26
--   eleconomista_archivo       12      24          12
--
-- 38 filas sobrantes sobre 2.704 (1,4%), que sostenían 94 correlaciones.
-- Poco en volumen, pero contaminan justo donde duele: el contexto del brief
-- repite la misma nota, y la muestra del backtest de ADR-21 pierde
-- independencia ANTES de que el cálculo de n efectiva pueda descontarla.
--
-- Por qué NO se cambia la fórmula del guid: re-clavearía las 2.704 noticias,
-- obligaría a reenriquecer el corpus entero (~78 min de GPU) y rompería la
-- trazabilidad con los lotes de Bronze ya archivados. La clave de contenido se
-- añade AL LADO de la de URL, no en su lugar.

-- 1. Índice de la clave de contenido. NO es UNIQUE a propósito: la ingesta es
--    fail-soft por fuente (invariante 6 del PRD) y una restricción dura
--    abortaría el lote entero ante una condición de calidad de la fuente que
--    ya sabemos tratar. El guard vive en `db.cargar_noticias`, que omite y
--    cuenta; el índice solo hace que esa comprobación sea barata.
CREATE INDEX IF NOT EXISTS idx_silver_news_contenido
    ON silver_news (source, title, published_at);

-- 2. Colapsar lo ya acumulado. Canónica = la que YA tiene inferencia en Gold
--    (esa costó GPU y conserva sus correlaciones); a igualdad, el guid menor,
--    que es determinista y hace el reproceso reproducible.
--
--    El borrado cae en cascada a gold_enriched_news y de ahí a
--    gold_news_market_corr, así que no quedan huérfanos.
DO $$
DECLARE
    sobrantes BIGINT;
BEGIN
    WITH canonicas AS (
        SELECT DISTINCT ON (n.source, n.title, n.published_at)
               n.guid
        FROM silver_news n
        LEFT JOIN gold_enriched_news g ON g.guid = n.guid
        ORDER BY n.source, n.title, n.published_at,
                 (g.guid IS NOT NULL) DESC, n.guid
    ),
    duplicadas AS (
        SELECT n.guid
        FROM silver_news n
        JOIN (
            SELECT source, title, published_at
            FROM silver_news
            GROUP BY 1, 2, 3
            HAVING COUNT(*) > 1
        ) d USING (source, title, published_at)
        WHERE n.guid NOT IN (SELECT guid FROM canonicas)
    ),
    eliminadas AS (
        DELETE FROM silver_news n
        USING duplicadas x
        WHERE n.guid = x.guid
        RETURNING n.guid
    )
    SELECT COUNT(*) INTO sobrantes FROM eliminadas;

    RAISE NOTICE 'silver_news: % filas sobrantes del mismo artículo eliminadas', sobrantes;
END $$;
