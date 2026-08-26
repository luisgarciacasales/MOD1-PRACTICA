-- Registro de lotes de Bronze ya validados, para que `validate` procese solo
-- lo nuevo (26-ago-2026).
--
-- El problema: `validate` revalidaba los 224 lotes de Bronze en cada corrida —
-- 373.800 registros, de los que el 97% son mercado/macro. `yahoo_finance`
-- re-descarga la serie histórica completa a diario, así que sus 8.520 registros
-- se revalidaban 31 veces para producir 15.014 filas en Silver. La etapa había
-- pasado de 115 s (15-ago) a 214 s y crecía linealmente con Bronze.
--
-- La marca se escribe en la MISMA transacción que la carga del lote: si
-- `validate` aborta, el lote no queda marcado y se reintenta en la corrida
-- siguiente. No hay estado a medias.
--
-- `batch_uuid` es la clave: lo genera `ingest` por lote y ya viaja en
-- metadata.json, así que identifica el lote sin depender de la ruta.
--
-- OJO — Silver sigue siendo reconstruible, pero ya no con `validate` a secas:
-- hay que usar `validate --todo`, que ignora esta tabla. Es lo que debe
-- correrse tras un cambio de contrato, y lo que usa el check de idempotencia
-- de `verify` (si llamara a `validate` sin más, el check pasaría por no hacer
-- nada en vez de por demostrar que el UPSERT funciona).

CREATE TABLE IF NOT EXISTS bronze_lotes_procesados (
    batch_uuid    UUID        PRIMARY KEY,
    source        TEXT        NOT NULL,
    ruta          TEXT        NOT NULL,
    procesado_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    filas_nuevas  INTEGER     NOT NULL DEFAULT 0,
    filas_actualizadas INTEGER NOT NULL DEFAULT 0,
    rechazos      INTEGER     NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_bronze_lotes_source
    ON bronze_lotes_procesados (source, procesado_at DESC);
