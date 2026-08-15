-- Ampliación 15-ago-2026: reporte narrativo trimestral por emisora (piloto de
-- 3: Banorte, Regional/Banregio, Bolsa Mexicana de Valores). Se trata como
-- una fuente de noticias más — mismo patrón que 005_google_news.sql.

ALTER TABLE silver_news DROP CONSTRAINT IF EXISTS silver_news_source_valida;

ALTER TABLE silver_news ADD CONSTRAINT silver_news_source_valida
    CHECK (source IN ('bmv_eventos', 'financiero', 'economista', 'bloomberg',
                      'google_news', 'reportes_ir'));
