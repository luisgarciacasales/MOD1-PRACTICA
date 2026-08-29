-- Precedencia de fuente en silver_fundamentals (29-ago-2026).
--
-- El backfill desde los PDF de resultados era EFÍMERO y de forma no
-- determinista: `validate --todo` revalida todo Bronze, y los lotes de
-- `yahoo_fundamentals` sobrescribían lo cargado del reporte oficial. Como
-- `verify` ejecuta `validate --todo` por dentro (para que su check de
-- idempotencia signifique algo), **cada verify deshacía el backfill**.
--
-- Comprobado el 29-ago con un experimento directo sobre GFNORTEO 2025-03-31:
--   backfill        → UPA 5.435  (lo que dice el reporte)
--   validate --todo → UPA 5.000  (lo que dice Yahoo, redondeado)
--
-- El estado dependía de cuándo se hubiera corrido verify por última vez.
--
-- Se resuelve declarando la precedencia en los DATOS, no en el orden de
-- ejecución: una fila de origen `reporte_pdf` no la pisa un UPSERT de `yahoo`.
-- Es el mismo principio de ADR-17 (la fuente con más contexto manda), aplicado
-- aquí a la fuente primaria frente al agregador.
--
-- El default es 'yahoo' porque es de donde viene todo lo cargado hasta hoy.

ALTER TABLE silver_fundamentals
    ADD COLUMN IF NOT EXISTS fuente TEXT NOT NULL DEFAULT 'yahoo';

ALTER TABLE silver_fundamentals_anual
    ADD COLUMN IF NOT EXISTS fuente TEXT NOT NULL DEFAULT 'yahoo';

COMMENT ON COLUMN silver_fundamentals.fuente IS
    'Origen del dato: yahoo (agregador) o reporte_pdf (estado financiero '
    'oficial de la emisora). Un UPSERT de yahoo NO pisa una fila de '
    'reporte_pdf — ver _SQL_FUNDAMENTALES en src/pipeline/db.py.';
