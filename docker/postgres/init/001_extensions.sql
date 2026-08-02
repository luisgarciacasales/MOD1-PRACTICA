-- Init de infraestructura de PostgreSQL — SOLO extensiones.
--
-- Este script corre UNA sola vez, cuando el volumen pgdata está vacío.
-- Deliberadamente NO contiene el DDL de las tablas del medallón: ese esquema
-- lo gobiernan los skills `data-contracts` (contratos Silver) y
-- `medallion-pipeline` (tablas Gold), donde vive junto a su validación.
-- Mezclarlo aquí lo volvería invisible para esos skills y difícil de versionar.

-- pgvector: requerido por gold_enriched_news.embedding vector(1024)
-- (PRD §5.3, modelo intfloat/multilingual-e5-large).
CREATE EXTENSION IF NOT EXISTS vector;

-- pg_trgm: búsqueda por similitud de texto sobre títulos/entidades. Útil para
-- el cross-reference con el diccionario Finnovista, donde los nombres
-- comerciales aparecen con variantes ("Nu", "Nubank", "Nu México").
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- unaccent: los medios mexicanos escriben con y sin acentos de forma
-- inconsistente ("Banorte" / "México" / "Mexico"). Normaliza los matches.
CREATE EXTENSION IF NOT EXISTS unaccent;
