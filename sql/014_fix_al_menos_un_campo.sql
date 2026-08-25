-- Bug encontrado al desplegar 012_shares_outstanding.sql: el contrato
-- Pydantic (src/contracts/fundamentals.py) se actualizó para aceptar una
-- fila con SOLO acciones_en_circulacion, pero el CHECK de la base (defensa
-- en profundidad de 007_fundamentals.sql / 009_fundamentals_anual.sql) se
-- quedó con la lista vieja de 8 campos — un dato real de ACTINVRB.MX
-- (2025-09-30, solo acciones_en_circulacion) pasó el contrato y reventó el
-- INSERT. Los dos deben decir lo mismo; aquí se sincroniza la base.

ALTER TABLE silver_fundamentals
    DROP CONSTRAINT silver_fundamentals_al_menos_un_campo;
ALTER TABLE silver_fundamentals
    ADD CONSTRAINT silver_fundamentals_al_menos_un_campo CHECK (
        ingresos_totales IS NOT NULL OR utilidad_neta IS NOT NULL OR
        utilidad_por_accion IS NOT NULL OR activo_total IS NOT NULL OR
        pasivo_total IS NOT NULL OR capital_contable IS NOT NULL OR
        acciones_en_circulacion IS NOT NULL OR
        flujo_operativo IS NOT NULL OR flujo_libre IS NOT NULL
    );

ALTER TABLE silver_fundamentals_anual
    DROP CONSTRAINT silver_fundamentals_anual_al_menos_un_campo;
ALTER TABLE silver_fundamentals_anual
    ADD CONSTRAINT silver_fundamentals_anual_al_menos_un_campo CHECK (
        ingresos_totales IS NOT NULL OR utilidad_neta IS NOT NULL OR
        utilidad_por_accion IS NOT NULL OR activo_total IS NOT NULL OR
        pasivo_total IS NOT NULL OR capital_contable IS NOT NULL OR
        acciones_en_circulacion IS NOT NULL OR
        flujo_operativo IS NOT NULL OR flujo_libre IS NOT NULL
    );
