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
    # La página es una SPA: el listado lo pinta JavaScript y el HTML servido no
    # trae tabla. Los endpoints internos probados devuelven 404 en el gateway
    # WSO2. Es el riesgo nº3 del PRD §9 ("scraping BMV frágil", probabilidad
    # Alta) materializándose; el fail-soft por fuente está para esto.
    Fuente(
        "bmv_eventos",
        "BMV — Eventos Relevantes",
        "noticias",
        "https://www.bmv.com.mx/es/Grupo_BMV/Eventos_relevantes",
    ),
    # 3.2 — RSS de medios financieros: más limpios que la propia BMV.
    # URLs verificadas desde el contenedor el 2026-08-01 (ver ADR-11).
    Fuente(
        "financiero",
        "El Financiero",
        "noticias",
        # Aquí sí se usa el feed general, al revés que en Bloomberg Línea: las
        # categorías de El Financiero están casi vacías (mercados 2 entradas,
        # economía 3, empresas 1). El general da 100 con un 16% de señal, que
        # en términos absolutos son 16 noticias frente a 2. Se filtra aguas
        # abajo, nunca en la ingesta: Bronze no aplica criterios de selección.
        "https://www.elfinanciero.com.mx/rss/",
    ),
    Fuente(
        "economista",
        "El Economista — Mercados",
        "noticias",
        # HTTP 403 desde el servidor incluso con cabeceras de navegador: el WAF
        # bloquea IPs de datacenter. Se deja configurada porque el fail-soft la
        # cubre y puede desbloquearse desde otra red o con otro acuerdo de uso.
        "https://www.eleconomista.com.mx/rss/mercados",
    ),
    Fuente(
        "bloomberg",
        "Bloomberg Línea — Mercados",
        "noticias",
        # El sufijo ?outputType=xml es imprescindible: sin él, el mismo path
        # devuelve 404.
        #
        # Categoría `mercados`, no el feed general. Medido sobre 100 entradas
        # con la ruta real del pipeline: 69% llega a Silver frente al 55% del
        # general, y 4 noticias con ticker identificado frente a 1. El general
        # mezcla espectáculos y deportes, y además es panregional.
        "https://www.bloomberglinea.com/arc/outboundfeeds/rss/category/mercados/?outputType=xml",
    ),
    # 3.3 — diccionario estático, carga única con actualización manual.
    Fuente("finnovista", "Finnovista Radar Fintech México", "diccionario", None),
    # 3.4 y 3.5 — datos estructurados; contrato de tipos y rangos, no semántico.
    Fuente("yahoo_finance", "Yahoo Finance (yfinance)", "mercado", None),
    Fuente("banxico", "BANXICO SIE", "mercado", None),
)

# Fuentes con perfil macroeconómico: publican notas de política monetaria que
# legítimamente no mencionan ningún ticker.
#
# NO son un pase libre al bypass (ADR-10). Solo REFUERZAN la señal léxica:
# bajan el umbral de MIN_TERMINOS_MACRO términos a 1. Sin ningún término macro
# en el texto, el bypass no se activa ni siquiera para estas fuentes.
FUENTES_CON_BYPASS_MACRO: frozenset[str] = frozenset({"bloomberg"})

FUENTES_POR_ID: dict[str, Fuente] = {f.id: f for f in FUENTES}
