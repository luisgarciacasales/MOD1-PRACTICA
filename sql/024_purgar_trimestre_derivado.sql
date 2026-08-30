-- Purga del trimestre que Yahoo derivó mal (29-ago-2026).
--
-- Va como migración y no como un DELETE suelto en psql por trazabilidad: el
-- contrato ya rechaza `ingresos_totales < 0` (ver src/contracts/fundamentals.py),
-- pero un contrato nuevo no alcanza a las filas escritas bajo el anterior, y
-- sin este borrado GFNORTEO arrastraba un ROE de -1.1 por ciento entre el
-- 2025-08-14 y el 2026-02-15 — para un trimestre en que el banco ganó unos
-- 14,600 mdp.
--
-- Qué tenía de malo la fila. Yahoo construye el trimestre de las emisoras
-- mexicanas restando periodos en vez de observarlo, y aquí los operandos no
-- casaban: ingresos -13,555 mdp y utilidad neta -670 mdp, con las seis últimas
-- cifras IDÉNTICAS a las del trimestre anterior en ambos campos
-- (...361,681 y ...111,461). Esa coincidencia es la firma aritmética de la
-- resta, no ruido de la fuente. Unas ventas negativas no son un dato.
--
-- El rastro no se pierde: la fila queda en `silver_dead_letters` con su motivo
-- cada vez que se revalida el lote de Bronze, que sigue siendo inmutable.
--
-- Idempotente y auto-limitada: se escribe como condición sobre el DATO, no
-- sobre el (ticker, period_end), de modo que si mañana Yahoo publica la misma
-- basura para otra emisora esto también la alcanza, y sobre una base
-- reconstruida desde cero no borra nada porque el contrato ya no la deja
-- entrar.

DELETE FROM silver_fundamentals       WHERE ingresos_totales < 0;
DELETE FROM silver_fundamentals_anual WHERE ingresos_totales < 0;
