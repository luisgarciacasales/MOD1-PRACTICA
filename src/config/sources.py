"""Definición de las 5 fuentes de datos de Fase 1 (PRD §3).

La ingesta es *fail-soft por fuente* (PRD §4.4): si la BMV cambia su DOM y el
scraper revienta, las otras cuatro fuentes deben completar su batch igual. Por
eso cada fuente se declara aquí de forma independiente, sin dependencias entre sí.
"""

from typing import Literal, NamedTuple

Categoria = Literal["noticias", "mercado", "diccionario"]

# Coincide con el enum `source` del esquema Bronze (PRD §5.1) y de silver_news.
SourceId = Literal[
    "bmv_eventos",
    "financiero",
    "economista",
    "bloomberg",
    "finnovista",
    "yahoo_finance",
    "banxico",
]


class Fuente(NamedTuple):
    id: str
    nombre: str
    categoria: Categoria
    url: str | None


FUENTES: tuple[Fuente, ...] = (
    # 3.1 — la fuente de menor estructura: tickers embebidos en texto libre.
    Fuente(
        "bmv_eventos",
        "BMV — Eventos Relevantes",
        "noticias",
        "https://www.bmv.com.mx/es/emisoras/eventos-relevantes",
    ),
    # 3.2 — RSS de medios financieros: más limpios que la propia BMV.
    # URLs a confirmar contra el feed vivo durante la implementación de la
    # ingesta; se dejan aquí como punto único de configuración.
    Fuente(
        "financiero",
        "El Financiero — Mercados",
        "noticias",
        "https://www.elfinanciero.com.mx/arc/outboundfeeds/rss/category/mercados/",
    ),
    Fuente(
        "economista",
        "El Economista — Mercados",
        "noticias",
        "https://www.eleconomista.com.mx/rss/mercados",
    ),
    Fuente(
        "bloomberg",
        "Bloomberg Línea México",
        "noticias",
        "https://www.bloomberglinea.com/arc/outboundfeeds/rss/",
    ),
    # 3.3 — diccionario estático, carga única con actualización manual.
    Fuente("finnovista", "Finnovista Radar Fintech México", "diccionario", None),
    # 3.4 y 3.5 — datos estructurados; contrato de tipos y rangos, no semántico.
    Fuente("yahoo_finance", "Yahoo Finance (yfinance)", "mercado", None),
    Fuente("banxico", "BANXICO SIE", "mercado", None),
)

# Fuente `bloomberg` tiene tratamiento especial en el contrato Silver: es una de
# las señales que activan el bypass macroeconómico (PRD §6.2), porque publica
# notas de política monetaria que legítimamente no mencionan ningún ticker.
FUENTES_CON_BYPASS_MACRO: frozenset[str] = frozenset({"bloomberg"})

FUENTES_POR_ID: dict[str, Fuente] = {f.id: f for f in FUENTES}
