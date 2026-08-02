"""Etapa 6 — Correlación noticias ↔ mercado. PRD §4.4 paso 6, §6.6.

JOIN temporal entre `gold_enriched_news` y `gold_market_prices` resolviendo el
**siguiente día hábil** con el calendario XMEX. Dos modalidades:

· **Directo** — la noticia menciona una emisora que cotiza en la BMV.
· **Proxy** — la noticia habla de una fintech que NO cotiza (Nu, Stori, Ualá).
  El sector afectado se traduce a la emisora listada más expuesta
  (`src/config/tickers.py`), se marca `is_proxy = true` y se conserva la
  fintech original para no perder la trazabilidad.

Los tickers se toman de **la unión de dos fuentes**: los que extrajo el NER del
LLM y los que identificó el léxico en Silver. El esquema del PRD §5.3 lo
contempla explícitamente — "ticker detectado por NER **o fuente**" — y en la
práctica importa: sobre el corpus actual el léxico encuentra 7 emisoras y el
NER solo 1, así que quedarse con el NER descartaría la mayoría del análisis.

    docker compose exec -T app python -m src.pipeline.correlate
    docker compose exec -T app python -m src.pipeline.correlate --date 2026-07-31
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import date
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.config.emisoras import ALIAS_EMISORAS
from src.config.tickers import SECTOR_A_PROXY
from src.pipeline import db
from src.pipeline.calendario import desplazar_habiles, siguiente_dia_habil

TICKERS_VALIDOS = frozenset(ALIAS_EMISORAS)

# Horizonte del cambio acumulado post-noticia, en días de cotización (PRD §5.3).
HORIZONTE_DIAS = 5

_SQL_NOTICIAS = """
SELECT g.guid,
       (g.published_at AT TIME ZONE 'America/Mexico_City')::date AS news_date,
       g.ner_tickers,
       g.fintechs_identified,
       g.sector_affected,
       s.tickers  AS lex_tickers,
       s.sector   AS lex_sector,
       s.entities AS lex_entities
FROM gold_enriched_news g
JOIN silver_news s USING (guid)
WHERE (%(fecha)s::date IS NULL
       OR (g.published_at AT TIME ZONE 'America/Mexico_City')::date = %(fecha)s::date)
ORDER BY g.published_at DESC
"""

_SQL_UPSERT = """
INSERT INTO gold_news_market_corr (
    news_guid, ticker, is_proxy, proxy_ticker, original_fintech, sector_affected,
    news_date, next_trading_day, price_date,
    close_price, next_day_return_pct, price_change_5d_pct, macro_context, correlated_at
) VALUES (
    %(news_guid)s, %(ticker)s, %(is_proxy)s, %(proxy_ticker)s, %(original_fintech)s,
    %(sector_affected)s, %(news_date)s, %(next_trading_day)s, %(price_date)s,
    %(close_price)s, %(next_day_return_pct)s, %(price_change_5d_pct)s,
    %(macro_context)s, NOW()
)
ON CONFLICT (news_guid, ticker, price_date) DO UPDATE SET
    is_proxy = EXCLUDED.is_proxy, proxy_ticker = EXCLUDED.proxy_ticker,
    original_fintech = EXCLUDED.original_fintech,
    sector_affected = EXCLUDED.sector_affected,
    close_price = EXCLUDED.close_price,
    next_day_return_pct = EXCLUDED.next_day_return_pct,
    price_change_5d_pct = EXCLUDED.price_change_5d_pct,
    macro_context = EXCLUDED.macro_context,
    correlated_at = NOW()
-- Con `row_factory=dict_row` una expresión sin alias llega como "?column?".
RETURNING (xmax = 0) AS insertada
"""


def objetivos(fila: dict[str, Any], fintechs_conocidas: set[str]) -> list[dict[str, Any]]:
    """Contra qué tickers se debe correlacionar esta noticia.

    Prioridad: si hay emisora directa, se usa esa. El proxy es un **sustituto**
    para cuando no hay ninguna, no un añadido — si la noticia ya menciona a
    Banorte, medir además su sector sobre Banorte duplicaría la misma señal.
    """
    directos = {
        t for t in (fila["ner_tickers"] or []) + (fila["lex_tickers"] or [])
        if t in TICKERS_VALIDOS
    }
    if directos:
        return [
            {"ticker": t, "is_proxy": False, "proxy_ticker": None,
             "original_fintech": None, "sector_affected": None}
            for t in sorted(directos)
        ]

    # --- Proxy ticker (PRD §3.3) --------------------------------------------
    detectadas = list(fila["fintechs_identified"] or [])
    if not detectadas:
        # El NER no la vio pero el léxico de Silver sí: las entidades incluyen
        # los nombres del diccionario Finnovista.
        detectadas = [e for e in (fila["lex_entities"] or []) if e in fintechs_conocidas]
    if not detectadas:
        return []

    sector = fila["sector_affected"] or fila["lex_sector"]
    proxies = SECTOR_A_PROXY.get(sector or "")
    if not proxies:
        # Fintech sin sector resoluble: no hay forma honesta de asignarle un
        # precio. Se deja sin correlacionar en vez de inventar una emisora.
        return []

    return [
        {"ticker": p, "is_proxy": True, "proxy_ticker": p,
         "original_fintech": detectadas[0], "sector_affected": sector}
        for p in proxies
    ]


def _macro_en(cur, fecha: date) -> dict[str, Any]:
    """Tasas y tipo de cambio vigentes en `fecha`: el último dato publicado
    hasta ese día, por serie. `DISTINCT ON` es la forma barata de hacerlo."""
    cur.execute(
        """
        SELECT DISTINCT ON (series_id) series_id, series_name, value, date
        FROM gold_macro_indicators
        WHERE date <= %s
        ORDER BY series_id, date DESC
        """,
        (fecha,),
    )
    return {
        f["series_id"]: {
            "nombre": f["series_name"],
            "valor": f["value"],
            "fecha": f["date"].isoformat(),
        }
        for f in cur.fetchall()
    }


def ejecutar(*, fecha: date | None) -> int:
    with db.conectar() as conexion, conexion.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(_SQL_NOTICIAS, {"fecha": fecha})
        noticias = cur.fetchall()
        cur.execute("SELECT commercial_name FROM silver_fintech_dict")
        fintechs = {f["commercial_name"] for f in cur.fetchall()}

        if not noticias:
            print("[correlate] no hay noticias enriquecidas que correlacionar")
            return 1

        print(f"[correlate] {len(noticias)} noticias · calendario XMEX")

        # El contexto macro se cachea por fecha: decenas de noticias comparten
        # día y la consulta es idéntica para todas.
        cache_macro: dict[date, dict[str, Any]] = {}
        nuevas = actualizadas = 0
        motivos: Counter = Counter()

        for fila in noticias:
            news_date = fila["news_date"]
            destinos = objetivos(fila, fintechs)
            if not destinos:
                motivos["sin ticker ni proxy resoluble"] += 1
                continue

            siguiente = siguiente_dia_habil(news_date)
            if siguiente is None:
                motivos["fuera del calendario"] += 1
                continue

            if news_date not in cache_macro:
                cache_macro[news_date] = _macro_en(cur, news_date)
            macro = cache_macro[news_date]

            for destino in destinos:
                ticker = destino["ticker"]

                cur.execute(
                    """SELECT date, close, daily_return_pct
                       FROM gold_market_prices
                       WHERE ticker = %s AND date >= %s
                       ORDER BY date LIMIT 1""",
                    (ticker, siguiente),
                )
                precio = cur.fetchone()
                if precio is None:
                    # Ocurre con noticias de hoy: el mercado aún no ha cerrado.
                    # Se reintentará en el batch siguiente, cuando exista el
                    # precio; por eso no se marca nada como procesado.
                    motivos["sin precio posterior todavía"] += 1
                    continue

                price_date = precio["date"]
                fecha_5d = desplazar_habiles(price_date, HORIZONTE_DIAS)
                cambio_5d = None
                if fecha_5d:
                    cur.execute(
                        "SELECT close FROM gold_market_prices WHERE ticker = %s AND date = %s",
                        (ticker, fecha_5d),
                    )
                    posterior = cur.fetchone()
                    if posterior and precio["close"]:
                        cambio_5d = 100.0 * (posterior["close"] / precio["close"] - 1)

                cur.execute(_SQL_UPSERT, {
                    "news_guid": fila["guid"], **destino,
                    "news_date": news_date,
                    "next_trading_day": siguiente,
                    "price_date": price_date,
                    "close_price": precio["close"],
                    "next_day_return_pct": precio["daily_return_pct"],
                    "price_change_5d_pct": cambio_5d,
                    "macro_context": Jsonb(macro) if macro else None,
                })
                if cur.fetchone()["insertada"]:
                    nuevas += 1
                else:
                    actualizadas += 1
        conexion.commit()

    print()
    print(f"{'correlaciones nuevas':<30} {nuevas}")
    print(f"{'actualizadas':<30} {actualizadas}")
    for motivo, n in motivos.most_common():
        print(f"{'sin correlacionar — ' + motivo:<30} {n}")
    print()
    print(f"[correlate] filas_nuevas = {nuevas}  (reprocesar debe dar 0 — criterio PRD §8)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.correlate",
        description="JOIN temporal noticias↔precios con calendario XMEX.",
    )
    parser.add_argument("--date", dest="fecha", default=None,
                        help="Solo noticias de esta fecha (YYYY-MM-DD, hora de CDMX).")
    args = parser.parse_args(argv)
    return ejecutar(fecha=date.fromisoformat(args.fecha) if args.fecha else None)


if __name__ == "__main__":
    raise SystemExit(main())
