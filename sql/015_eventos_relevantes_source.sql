-- Sincroniza silver_news_source_valida con SourceNoticias tras retirar
-- bmv_eventos/economista y agregar eventos_relevantes (ADR-16, ver
-- docs/HARNESS.md). Mismo patrón que 005_google_news.sql y
-- 008_reportes_ir.sql: DROP + ADD, no ALTER, porque un CHECK no admite
-- modificar su expresión in place.
--
-- Bug encontrado al desplegar (25-ago-2026): el contrato Pydantic
-- (src/contracts/news.py, SourceNoticias) se actualizó pero este CHECK no —
-- mismo error que 014_fix_al_menos_un_campo.sql con fundamentales. Un
-- evento real de BBAJIOO.MX pasó el contrato y reventó el INSERT.

ALTER TABLE silver_news DROP CONSTRAINT IF EXISTS silver_news_source_valida;

ALTER TABLE silver_news ADD CONSTRAINT silver_news_source_valida
    CHECK (source IN ('eventos_relevantes', 'financiero', 'bloomberg',
                       'google_news', 'reportes_ir'));
