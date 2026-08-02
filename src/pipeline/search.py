"""API de consulta de la capa Gold (PRD §4.4 paso 7).

Expone las dos funciones que el PRD nombra explícitamente:

    search_semantic(query, top_k)   búsqueda semántica Top-K en español
    get_market_context(ticker)      precio, macro y noticias correlacionadas

Se usan desde `verify`, desde un notebook o desde `python -m src.pipeline.search`.

    docker compose exec -T app python -m src.pipeline.search "recorte de tasas de Banxico"
    docker compose exec -T app python -m src.pipeline.search --ticker GFNORTEO.MX
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any

import psycopg

from src.pipeline import db
from src.pipeline.embeddings import obtener_embebedor
from src.pipeline.index import cargar_indice


@dataclass
class Resultado:
    id: int
    guid: str
    score: float
    title: str
    url: str
    source: str
    published_at: Any
    sentiment_label: str | None
    ner_tickers: list[str]
    is_ma_event: bool


def search_semantic(query: str, top_k: int = 10) -> list[Resultado]:
    """Top-K por similitud coseno sobre el índice FAISS.

    El score es el producto interno de vectores normalizados, así que cae en
    [-1, 1] y se lee directamente como coseno.
    """
    indice = cargar_indice()
    if indice is None or indice.ntotal == 0:
        raise RuntimeError(
            "no hay índice FAISS: ejecuta `python -m src.pipeline.index` primero"
        )

    vector = obtener_embebedor().consulta(query)
    # FAISS devuelve -1 en las posiciones sobrantes si se piden más vecinos que
    # vectores hay; se acota para no tener que filtrarlos después.
    k = min(top_k, indice.ntotal)
    scores, ids = indice.search(vector, k)

    encontrados = [(int(i), float(s)) for i, s in zip(ids[0], scores[0], strict=True) if i != -1]
    if not encontrados:
        return []

    orden = {id_: pos for pos, (id_, _) in enumerate(encontrados)}
    puntajes = dict(encontrados)

    with db.conectar() as conexion, conexion.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """SELECT id, guid, title, url, source, published_at,
                      sentiment_label, ner_tickers, is_ma_event
               FROM gold_enriched_news WHERE id = ANY(%s)""",
            (list(orden),),
        )
        filas = cur.fetchall()

    # Se reordena en Python: el ANY() de SQL no preserva el orden de relevancia
    # que devolvió FAISS, y ese orden es justamente el resultado.
    filas.sort(key=lambda f: orden[f["id"]])
    return [
        Resultado(
            id=f["id"], guid=f["guid"], score=puntajes[f["id"]], title=f["title"],
            url=f["url"], source=f["source"], published_at=f["published_at"],
            sentiment_label=f["sentiment_label"], ner_tickers=f["ner_tickers"] or [],
            is_ma_event=f["is_ma_event"],
        )
        for f in filas
    ]


def get_market_context(ticker: str, dias: int = 30) -> dict[str, Any]:
    """Contexto de mercado de una emisora: último precio, macro vigente y las
    noticias ya correlacionadas con ella."""
    with db.conectar() as conexion, conexion.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(
            """SELECT date, close, daily_return_pct, ma_7d, ma_30d, volatility_30d
               FROM gold_market_prices WHERE ticker = %s ORDER BY date DESC LIMIT 1""",
            (ticker,),
        )
        ultimo = cur.fetchone()

        cur.execute(
            """SELECT c.news_date, c.price_date, c.is_proxy, c.original_fintech,
                      c.next_day_return_pct, c.price_change_5d_pct,
                      g.title, g.sentiment_label, g.is_ma_event
               FROM gold_news_market_corr c
               JOIN gold_enriched_news g ON g.guid = c.news_guid
               WHERE c.ticker = %s
               ORDER BY c.news_date DESC LIMIT 20""",
            (ticker,),
        )
        noticias = cur.fetchall()

        cur.execute(
            """SELECT DISTINCT ON (series_id) series_id, series_name, value, date
               FROM gold_macro_indicators ORDER BY series_id, date DESC"""
        )
        macro = cur.fetchall()

    return {"ticker": ticker, "ultimo_precio": ultimo, "noticias": noticias, "macro": macro}


def _imprimir_resultados(query: str, resultados: list[Resultado], ms: float) -> None:
    print(f'Consulta: "{query}"   ·   {len(resultados)} resultados en {ms:.0f} ms')
    print("-" * 100)
    for i, r in enumerate(resultados, 1):
        tickers = ",".join(r.ner_tickers) or "—"
        print(f"{i:>2}. [{r.score:.3f}] {r.title[:68]}")
        print(f"     {r.source} · {r.published_at:%Y-%m-%d} · {r.sentiment_label} "
              f"· tickers={tickers}" + ("  · M&A" if r.is_ma_event else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.search",
        description="Búsqueda semántica y contexto de mercado sobre la capa Gold.",
    )
    parser.add_argument("query", nargs="?", help="Consulta en español.")
    parser.add_argument("-k", "--top-k", type=int, default=10)
    parser.add_argument("--ticker", help="En vez de buscar, muestra el contexto de mercado.")
    args = parser.parse_args(argv)

    if args.ticker:
        ctx = get_market_context(args.ticker)
        precio = ctx["ultimo_precio"]
        print(f"=== {args.ticker} ===")
        if precio:
            print(f"último cierre {precio['date']}: {precio['close']:.2f} "
                  f"(ret {precio['daily_return_pct']:+.2f}% · vol30 "
                  f"{precio['volatility_30d'] or float('nan'):.2f})")
        print(f"noticias correlacionadas: {len(ctx['noticias'])}")
        for n in ctx["noticias"][:8]:
            marca = f" [proxy de {n['original_fintech']}]" if n["is_proxy"] else ""
            print(f"  {n['news_date']} · {n['sentiment_label']:<8} "
                  f"· ret {n['next_day_return_pct'] or 0:+.2f}%{marca} · {n['title'][:52]}")
        print("\nmacro vigente:")
        for m in ctx["macro"]:
            print(f"  {m['series_name'][:44]:<46} {m['value']:>10.4f}  ({m['date']})")
        return 0

    if not args.query:
        parser.error("indica una consulta o usa --ticker")

    inicio = time.perf_counter()
    resultados = search_semantic(args.query, args.top_k)
    ms = (time.perf_counter() - inicio) * 1000
    _imprimir_resultados(args.query, resultados, ms)
    if ms > 500:
        print(f"\nAVISO: {ms:.0f} ms supera el SLA de 500 ms del PRD §7.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
