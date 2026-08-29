"""Cargas idempotentes a PostgreSQL (PRD §6.3).

Toda escritura usa `INSERT ... ON CONFLICT (<clave natural>) DO UPDATE`, de modo
que reprocesar el mismo lote produce `filas_nuevas = 0`.

Cómo se cuentan las filas nuevas: PostgreSQL expone `xmax`, que vale 0 en una
fila recién insertada y distinto de 0 cuando el UPSERT resolvió un conflicto.
`RETURNING (xmax = 0) AS insertada` distingue las dos cosas en una sola pasada,
sin un SELECT previo que además sería susceptible de carreras.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import psycopg
from psycopg.rows import tuple_row

from src.config import get_settings
from src.contracts import (
    DeadLetter,
    FintechDictEntry,
    Fundamental,
    MacroIndicator,
    MarketPrice,
    SilverNews,
)


@dataclass
class Carga:
    """Resultado de una carga. `nuevas == 0` tras un reproceso es el criterio
    de idempotencia del PRD §8.

    `preservadas` cuenta las filas que el UPSERT dejó intactas a propósito
    porque la de la base viene de una fuente con precedencia (hoy, un dato de
    `reporte_pdf` que un lote de `yahoo` intentó pisar). No son actualizaciones
    —no se escribió nada— y contarlas como tales ocultaría justo lo que hay que
    ver: cuánto del lote entrante quedó descartado por precedencia."""

    nuevas: int = 0
    actualizadas: int = 0
    preservadas: int = 0

    @property
    def total(self) -> int:
        """Filas efectivamente escritas. Excluye `preservadas` por definición."""
        return self.nuevas + self.actualizadas

    def __iadd__(self, otra: Carga) -> Carga:
        self.nuevas += otra.nuevas
        self.actualizadas += otra.actualizadas
        self.preservadas += otra.preservadas
        return self


def conectar() -> psycopg.Connection:
    return psycopg.connect(get_settings().postgres_dsn, row_factory=tuple_row)


_SQL_NEWS = """
INSERT INTO silver_news (
    guid, source, title, content, url, published_at, ingested_at,
    tickers, sector, entities, enriched, macro_bypass, raw_batch_uuid
) VALUES (
    %(guid)s, %(source)s, %(title)s, %(content)s, %(url)s, %(published_at)s,
    %(ingested_at)s, %(tickers)s, %(sector)s, %(entities)s, %(enriched)s,
    %(macro_bypass)s, %(raw_batch_uuid)s
)
ON CONFLICT (guid) DO UPDATE SET
    title        = EXCLUDED.title,
    content      = EXCLUDED.content,
    tickers      = EXCLUDED.tickers,
    sector       = EXCLUDED.sector,
    entities     = EXCLUDED.entities,
    macro_bypass = EXCLUDED.macro_bypass
    -- `enriched` NO se pisa: si el LLM ya procesó esta noticia, un reproceso de
    -- la ingesta no puede obligar a pagar la inferencia otra vez.
RETURNING (xmax = 0)
"""

_SQL_PRECIOS = """
INSERT INTO silver_market_prices (
    ticker, date, open, high, low, close, adj_close, volume, ingested_at, raw_batch_uuid
) VALUES (
    %(ticker)s, %(date)s, %(open)s, %(high)s, %(low)s, %(close)s, %(adj_close)s,
    %(volume)s, %(ingested_at)s, %(raw_batch_uuid)s
)
ON CONFLICT (ticker, date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, adj_close = EXCLUDED.adj_close, volume = EXCLUDED.volume
RETURNING (xmax = 0)
"""

_SQL_MACRO = """
INSERT INTO silver_macro_indicators (
    series_id, date, value, ingested_at, raw_batch_uuid
) VALUES (%(series_id)s, %(date)s, %(value)s, %(ingested_at)s, %(raw_batch_uuid)s)
ON CONFLICT (series_id, date) DO UPDATE SET value = EXCLUDED.value
RETURNING (xmax = 0)
"""

_SQL_FUNDAMENTALES = """
INSERT INTO silver_fundamentals (
    ticker, period_end, ingresos_totales, utilidad_neta, utilidad_por_accion,
    activo_total, pasivo_total, capital_contable, acciones_en_circulacion,
    flujo_operativo, flujo_libre, fuente, ingested_at, raw_batch_uuid
) VALUES (
    %(ticker)s, %(period_end)s, %(ingresos_totales)s, %(utilidad_neta)s,
    %(utilidad_por_accion)s, %(activo_total)s, %(pasivo_total)s,
    %(capital_contable)s, %(acciones_en_circulacion)s,
    %(flujo_operativo)s, %(flujo_libre)s, %(fuente)s,
    %(ingested_at)s, %(raw_batch_uuid)s
)
ON CONFLICT (ticker, period_end) DO UPDATE SET
    ingresos_totales        = EXCLUDED.ingresos_totales,
    utilidad_neta           = EXCLUDED.utilidad_neta,
    utilidad_por_accion     = EXCLUDED.utilidad_por_accion,
    activo_total            = EXCLUDED.activo_total,
    pasivo_total            = EXCLUDED.pasivo_total,
    capital_contable        = EXCLUDED.capital_contable,
    acciones_en_circulacion = EXCLUDED.acciones_en_circulacion,
    flujo_operativo         = EXCLUDED.flujo_operativo,
    flujo_libre             = EXCLUDED.flujo_libre,
    fuente                  = EXCLUDED.fuente
-- Un dato del agregador NO pisa uno del reporte oficial de la emisora.
-- Sin esta condición el backfill desde PDF era efímero: `validate --todo`
-- revalida todo Bronze y devolvía la fila a los valores de Yahoo, así que
-- cada `verify` (que corre validate --todo por dentro) lo deshacía. Ver
-- sql/022_fundamentales_fuente.sql para el experimento que lo demostró.
WHERE silver_fundamentals.fuente <> 'reporte_pdf'
   OR EXCLUDED.fuente = 'reporte_pdf'
RETURNING (xmax = 0)
"""

# Misma forma que _SQL_FUNDAMENTALES, tabla distinta (ver
# sql/009_fundamentals_anual.sql para el porqué de no compartir tabla con la
# trimestral). Se escribe completa, no derivada por sustitución de texto —
# mismo criterio que el resto de este módulo.
_SQL_FUNDAMENTALES_ANUAL = """
INSERT INTO silver_fundamentals_anual (
    ticker, period_end, ingresos_totales, utilidad_neta, utilidad_por_accion,
    activo_total, pasivo_total, capital_contable, acciones_en_circulacion,
    flujo_operativo, flujo_libre, fuente, ingested_at, raw_batch_uuid
) VALUES (
    %(ticker)s, %(period_end)s, %(ingresos_totales)s, %(utilidad_neta)s,
    %(utilidad_por_accion)s, %(activo_total)s, %(pasivo_total)s,
    %(capital_contable)s, %(acciones_en_circulacion)s,
    %(flujo_operativo)s, %(flujo_libre)s, %(fuente)s,
    %(ingested_at)s, %(raw_batch_uuid)s
)
ON CONFLICT (ticker, period_end) DO UPDATE SET
    ingresos_totales        = EXCLUDED.ingresos_totales,
    utilidad_neta           = EXCLUDED.utilidad_neta,
    utilidad_por_accion     = EXCLUDED.utilidad_por_accion,
    activo_total            = EXCLUDED.activo_total,
    pasivo_total            = EXCLUDED.pasivo_total,
    capital_contable        = EXCLUDED.capital_contable,
    acciones_en_circulacion = EXCLUDED.acciones_en_circulacion,
    flujo_operativo         = EXCLUDED.flujo_operativo,
    flujo_libre             = EXCLUDED.flujo_libre,
    fuente                  = EXCLUDED.fuente
WHERE silver_fundamentals_anual.fuente <> 'reporte_pdf'
   OR EXCLUDED.fuente = 'reporte_pdf'
RETURNING (xmax = 0)
"""

_SQL_FINTECH = """
INSERT INTO silver_fintech_dict (
    legal_name, commercial_name, ticker, sector, country, updated_at
) VALUES (
    %(legal_name)s, %(commercial_name)s, %(ticker)s, %(sector)s, %(country)s, %(updated_at)s
)
ON CONFLICT (commercial_name, country) DO UPDATE SET
    legal_name = EXCLUDED.legal_name,
    ticker     = EXCLUDED.ticker,
    sector     = EXCLUDED.sector,
    updated_at = EXCLUDED.updated_at
RETURNING (xmax = 0)
"""

_SQL_DEAD_LETTER = """
INSERT INTO silver_dead_letters (
    guid, source, raw_payload, rejection_reason, rejection_detail,
    rejected_at, first_rejected_at, batch_uuid
) VALUES (
    %(guid)s, %(source)s, %(raw_payload)s, %(rejection_reason)s,
    %(rejection_detail)s, %(rejected_at)s, %(rejected_at)s, %(batch_uuid)s
)
ON CONFLICT (source, guid) WHERE guid IS NOT NULL DO UPDATE SET
    raw_payload      = EXCLUDED.raw_payload,
    rejection_reason = EXCLUDED.rejection_reason,
    rejection_detail = EXCLUDED.rejection_detail,
    rejected_at       = EXCLUDED.rejected_at,
    times_rejected     = silver_dead_letters.times_rejected + 1,
    batch_uuid         = EXCLUDED.batch_uuid
"""


def cargar_noticias(cur: psycopg.Cursor, filas: list[SilverNews]) -> Carga:
    return _cargar(cur, _SQL_NEWS, [_dump(f) for f in filas])


def cargar_precios(cur: psycopg.Cursor, filas: list[MarketPrice]) -> Carga:
    return _cargar(cur, _SQL_PRECIOS, [_dump(f) for f in filas])


def cargar_macro(cur: psycopg.Cursor, filas: list[MacroIndicator]) -> Carga:
    return _cargar(cur, _SQL_MACRO, [_dump(f) for f in filas])


def cargar_fundamentales(cur: psycopg.Cursor, filas: list[Fundamental]) -> Carga:
    return _cargar(cur, _SQL_FUNDAMENTALES, [_dump(f) for f in filas])


def cargar_fundamentales_anual(cur: psycopg.Cursor, filas: list[Fundamental]) -> Carga:
    """Mismo modelo `Fundamental` que la trimestral — solo cambia la tabla
    destino (ver `src/contracts/fundamentals.py`)."""
    return _cargar(cur, _SQL_FUNDAMENTALES_ANUAL, [_dump(f) for f in filas])


def cargar_fintech(cur: psycopg.Cursor, filas: list[FintechDictEntry]) -> Carga:
    return _cargar(cur, _SQL_FINTECH, [_dump(f) for f in filas])


def cargar_dead_letters(cur: psycopg.Cursor, filas: list[DeadLetter]) -> int:
    """La cuarentena deduplica por `(source, guid)` (diagnóstico 2026-08-14):
    algunas fuentes reenvían casi el mismo lote en cada ingesta, y sin UPSERT
    cada rechazo repetido generaba una fila nueva — hasta 52x el mismo GUID en
    `bloomberg`, sin aportar información.

    La señal que la cuarentena quería capturar —que algo lleva días
    rechazándose— se conserva, pero como contador (`times_rejected`) en vez de
    como filas repetidas. Un registro sin `guid` no tiene clave natural para
    agregar y sigue insertándose como fila nueva cada vez."""
    from psycopg.types.json import Jsonb

    for fila in filas:
        datos = _dump(fila)
        datos["raw_payload"] = Jsonb(fila.raw_payload)
        datos["rejection_reason"] = fila.rejection_reason.value
        cur.execute(_SQL_DEAD_LETTER, datos)
    return len(filas)


def lotes_procesados(cur: psycopg.Cursor) -> set[UUID]:
    """`batch_uuid` de los lotes de Bronze ya validados.

    Lo consume `validate` para saltarse lo que ya cargó. Se lee de una vez en
    vez de consultar lote a lote: son cientos de UUID, caben de sobra en
    memoria, y así la decisión de saltar no cuesta una ida a la base por lote.
    """
    cur.execute("SELECT batch_uuid FROM bronze_lotes_procesados")
    return {fila[0] for fila in cur.fetchall()}


def marcar_lote_procesado(
    cur: psycopg.Cursor,
    batch_uuid: UUID,
    *,
    source: str,
    ruta: str,
    carga: Carga,
    rechazos: int,
) -> None:
    """Deja constancia de que el lote ya se validó.

    Va en la misma transacción que la carga, así que un fallo posterior lo
    deshace junto con las filas: no puede quedar marcado un lote cuyos datos no
    llegaron a Silver.

    El UPSERT es para `--todo`, que revalida lotes ya marcados y debe refrescar
    los conteos en vez de reventar por clave duplicada.
    """
    cur.execute(
        """
        INSERT INTO bronze_lotes_procesados (
            batch_uuid, source, ruta, filas_nuevas, filas_actualizadas, rechazos
        ) VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (batch_uuid) DO UPDATE SET
            procesado_at       = NOW(),
            filas_nuevas       = EXCLUDED.filas_nuevas,
            filas_actualizadas = EXCLUDED.filas_actualizadas,
            rechazos           = EXCLUDED.rechazos
        """,
        (batch_uuid, source, ruta, carga.nuevas, carga.actualizadas, rechazos),
    )


# --- Interno ---------------------------------------------------------------


def _cargar(cur: psycopg.Cursor, sql: str, filas: list[dict]) -> Carga:
    carga = Carga()
    for fila in filas:
        cur.execute(sql, fila)
        resultado = cur.fetchone()
        # Sin fila devuelta, el DO UPDATE no llegó a ejecutarse: su WHERE de
        # precedencia lo bloqueó. No es una actualización — no se escribió.
        if resultado is None:
            carga.preservadas += 1
        elif resultado[0]:
            carga.nuevas += 1
        else:
            carga.actualizadas += 1
    return carga


def _dump(modelo) -> dict:
    """Modelo Pydantic → dict de parámetros. `mode="python"` conserva los
    datetime y UUID como objetos, que es lo que psycopg sabe adaptar."""
    return modelo.model_dump(mode="python")
