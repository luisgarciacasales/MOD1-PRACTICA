-- Fuentes de archivo en el CHECK de silver_news (10-sep-2026).
--
-- Registrar una fuente de noticias exige tocar TRES listas que deben coincidir:
--
--   1. `FUENTES_NOTICIAS` en src/pipeline/validate.py  → qué lote se procesa
--   2. `SourceNoticias` en src/contracts/news.py       → qué source acepta el contrato
--   3. este CHECK                                      → qué source acepta la tabla
--
-- Ese día se tocó la primera y los 378 artículos recuperados del archivo se
-- fueron enteros a cuarentena con UNKNOWN_SOURCE. Se corrigió la segunda y
-- entonces reventó la tercera con CheckViolation. Tres listas y ningún
-- mecanismo que las obligue a estar de acuerdo.
--
-- Las dos de Python ahora tienen una prueba que las contrasta entre sí, y otra
-- de integración contrasta esta contra el contrato: no se pueden derivar unas
-- de otras —el CHECK vive en la base y protege también contra escrituras que
-- no pasen por el pipeline— pero sí se puede exigir que no discrepen.

ALTER TABLE silver_news DROP CONSTRAINT IF EXISTS silver_news_source_valida;

ALTER TABLE silver_news ADD CONSTRAINT silver_news_source_valida CHECK (
    source = ANY (ARRAY[
        -- Ingesta en vivo
        'eventos_relevantes', 'financiero', 'bloomberg', 'google_news', 'reportes_ir',
        -- Recuperadas del archivo histórico. Se distinguen por el sufijo
        -- `_archivo`: son noticias de años atrás traídas hoy, y confundirlas
        -- con lo ingerido en su momento falsearía cualquier análisis temporal.
        'eleconomista_archivo', 'elfinanciero_archivo'
    ]::text[])
);
