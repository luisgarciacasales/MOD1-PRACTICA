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

import psycopg
from psycopg.rows import tuple_row

from src.config import get_settings
from src.contracts import (
    DeadLetter,
    FintechDictEntry,
    MacroIndicator,
    MarketPrice,
    SilverNews,
)


@dataclass
class Carga:
    """Resultado de una carga. `nuevas == 0` tras un reproceso es el criterio
    de idempotencia del PRD §8."""

    nuevas: int = 0
    actualizadas: int = 0

    @property
    def total(self) -> int:
        return self.nuevas + self.actualizadas

    def __iadd__(self, otra: Carga) -> Carga:
        self.nuevas += otra.nuevas
        self.actualizadas += otra.actualizadas
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
    guid, source, raw_payload, rejection_reason, rejection_detail, rejected_at, batch_uuid
) VALUES (
    %(guid)s, %(source)s, %(raw_payload)s, %(rejection_reason)s,
    %(rejection_detail)s, %(rejected_at)s, %(batch_uuid)s
)
"""


def cargar_noticias(cur: psycopg.Cursor, filas: list[SilverNews]) -> Carga:
    return _cargar(cur, _SQL_NEWS, [_dump(f) for f in filas])


def cargar_precios(cur: psycopg.Cursor, filas: list[MarketPrice]) -> Carga:
    return _cargar(cur, _SQL_PRECIOS, [_dump(f) for f in filas])


def cargar_macro(cur: psycopg.Cursor, filas: list[MacroIndicator]) -> Carga:
    return _cargar(cur, _SQL_MACRO, [_dump(f) for f in filas])


def cargar_fintech(cur: psycopg.Cursor, filas: list[FintechDictEntry]) -> Carga:
    return _cargar(cur, _SQL_FINTECH, [_dump(f) for f in filas])


def cargar_dead_letters(cur: psycopg.Cursor, filas: list[DeadLetter]) -> int:
    """La cuarentena NO deduplica: cada rechazo es un evento con su propio
    momento y su propio motivo. Saber que el mismo registro se rechazó tres
    días seguidos es justo la señal que hace útil la tabla."""
    from psycopg.types.json import Jsonb

    for fila in filas:
        datos = _dump(fila)
        datos["raw_payload"] = Jsonb(fila.raw_payload)
        datos["rejection_reason"] = fila.rejection_reason.value
        cur.execute(_SQL_DEAD_LETTER, datos)
    return len(filas)


# --- Interno ---------------------------------------------------------------


def _cargar(cur: psycopg.Cursor, sql: str, filas: list[dict]) -> Carga:
    carga = Carga()
    for fila in filas:
        cur.execute(sql, fila)
        resultado = cur.fetchone()
        if resultado and resultado[0]:
            carga.nuevas += 1
        else:
            carga.actualizadas += 1
    return carga


def _dump(modelo) -> dict:
    """Modelo Pydantic → dict de parámetros. `mode="python"` conserva los
    datetime y UUID como objetos, que es lo que psycopg sabe adaptar."""
    return modelo.model_dump(mode="python")
