-- Descubierto al desplegar 010_valuation.sql (25-ago-2026): la UPA
-- TRIMESTRAL de Yahoo trae huecos estructurales en el sector financiero —
-- Q2 y a veces Q3 en blanco para Banorte, Inbursa, Quálitas, GFinbur
-- (verificado también contra "Basic EPS": mismo patrón de NULL, no es un
-- campo alternativo sin explorar). Sin 4 trimestres consecutivos, esas
-- emisoras nunca tendrían P/U bajo _SQL_VALUATION tal como se desplegó.
--
-- La UPA ANUAL (silver_fundamentals_anual, ver 009_fundamentals_anual.sql)
-- SÍ está completa para las mismas emisoras — así que sirve de respaldo
-- cuando no hay TTM trimestral. `eps_source` declara cuál se usó en cada
-- fila: un P/U de UPA anual (actualiza 1 vez al año) y uno de UPA TTM
-- trimestral (actualiza 4 veces al año) no son la misma medida, y esconder
-- cuál es cuál sería más engañoso que exponerlo. Ver
-- src/pipeline/transform.py (_SQL_VALUATION) para el COALESCE.

ALTER TABLE gold_valuation
    ADD COLUMN IF NOT EXISTS eps_source TEXT;

-- Backfill: las filas ya cargadas por el despliegue de 010 solo pudieron
-- venir de la ruta trimestral (la anual todavía no existía en la consulta).
UPDATE gold_valuation SET eps_source = 'trimestral_ttm' WHERE eps_source IS NULL;

ALTER TABLE gold_valuation ALTER COLUMN eps_source SET NOT NULL;

ALTER TABLE gold_valuation
    ADD CONSTRAINT gold_valuation_eps_source CHECK (eps_source IN ('trimestral_ttm', 'anual'));
