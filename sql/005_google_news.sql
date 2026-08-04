-- Añade `google_news` al conjunto de fuentes válidas de silver_news.
--
-- El CHECK original enumeraba las cuatro fuentes de texto del PRD §3.2. Google
-- News se incorpora el 2026-08-04 como respuesta al cuello de botella del
-- corpus: las dos fuentes que darían noticia bancaria mexicana están caídas
-- —El Economista bloqueado por WAF, BMV Eventos sin endpoint— y las dos que
-- funcionan son un feed generalista y uno panregional.
--
-- Las 14 consultas dirigidas viajan bajo este único valor. Una fuente por
-- consulta habría hecho de este CHECK una lista que cambia cada vez que se
-- ajusta el catálogo; la trazabilidad se conserva en el `_consulta` del payload
-- de Bronze.

ALTER TABLE silver_news DROP CONSTRAINT IF EXISTS silver_news_source_valida;

ALTER TABLE silver_news ADD CONSTRAINT silver_news_source_valida
    CHECK (source IN ('bmv_eventos', 'financiero', 'economista', 'bloomberg',
                      'google_news'));
