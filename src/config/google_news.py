"""Consultas a Google News RSS (fuente añadida el 2026-08-04).

Resuelve el cuello de botella del corpus: las dos fuentes que darían noticia
bancaria mexicana están caídas —El Economista bloqueado por WAF y los Eventos
Relevantes de la BMV sin endpoint— y las dos que funcionan son un feed
generalista y uno panregional. Google News permite consultar de forma dirigida,
gratis y sin WAF.

## La lección que costó medir dos veces

La primera medición dio «91% de señal» para la consulta `Banorte`. Estaba
midiendo si **nuestro léxico disparaba**, no si la noticia era relevante: Banorte
patrocina un estadio, así que planos de asientos y partidos de fútbol contienen
la palabra, detectan ticker y pasan el contrato.

Medido de nuevo sobre relevancia financiera real:

    Banorte  (crudo)               100 entradas    3% relevantes
    Banorte + banco                 56 entradas   20% relevantes
    "Grupo Financiero Banorte"       8 entradas   50% relevantes

De ahí la regla de este módulo: **razón social entre comillas**. Se cambia
volumen por precisión, y para el objetivo del proyecto la precisión vale más —
una noticia irrelevante que pasa el contrato consume inferencia del LLM y mete
ruido en las correlaciones.

## Limitación que no se puede sortear

Google News RSS **no entrega el cuerpo del artículo**: el campo `summary` es
solo un enlace. El NER y el sentimiento trabajan sobre el titular. Es un
intercambio aceptable —el titular concentra la carga semántica— pero explica por
qué esta fuente produce enriquecimientos menos ricos que un RSS completo.
"""

from __future__ import annotations

from typing import NamedTuple
from urllib.parse import quote

# Plantilla del feed. `hl`/`gl`/`ceid` fijan español de México: sin ellos Google
# devuelve resultados en inglés y de otros países.
PLANTILLA = (
    "https://news.google.com/rss/search?q={consulta}&hl=es-419&gl=MX&ceid=MX:es-419"
)

# Ventana temporal por defecto. Dos días y no uno: el batch corre una vez al día
# y un artículo publicado de noche podría quedar fuera de una ventana de 24 h.
# El solape lo resuelve la clave natural — el mismo artículo produce el mismo
# guid y el UPSERT no lo duplica.
VENTANA_DIAS: int = 2


class Consulta(NamedTuple):
    etiqueta: str
    """Nombre corto para diagnóstico; viaja al payload como `_consulta`."""
    terminos: str
    """Lo que se busca. Las comillas dobles fuerzan frase exacta en Google."""


# Una consulta por institución, con la razón social entre comillas.
CONSULTAS_INSTITUCIONES: tuple[Consulta, ...] = (
    Consulta("banorte", '"Grupo Financiero Banorte"'),
    Consulta("bbva_mexico", '"BBVA México" banco'),
    Consulta("santander_mexico", '"Santander México" banco'),
    Consulta("banbajio", '"Banco del Bajío"'),
    Consulta("banregio", '"Banregio" OR "Hey Banco"'),
    Consulta("gentera", '"Gentera" OR "Compartamos Banco"'),
    Consulta("inbursa", '"Grupo Financiero Inbursa" OR "Banco Inbursa"'),
    Consulta("bolsa_mexicana", '"Bolsa Mexicana de Valores" emisoras'),
    Consulta("qualitas", '"Quálitas"'),
)

# Consultas temáticas: capturan el fenómeno que el PRD quiere analizar —la
# competencia entre banca y fintechs— aunque no nombren una institución concreta.
CONSULTAS_TEMATICAS: tuple[Consulta, ...] = (
    Consulta("banca_fintech", '"banca" fintech México competencia'),
    Consulta("banca_digital", '"banca digital" México'),
    Consulta("regulacion", 'CNBV OR "ley fintech" México banca'),
    Consulta("morosidad", '"cartera vencida" OR morosidad banca México'),
    Consulta("neobancos", 'neobanco OR "Nu México" OR Stori OR Klar'),
)

CONSULTAS: tuple[Consulta, ...] = (*CONSULTAS_INSTITUCIONES, *CONSULTAS_TEMATICAS)


def url_de(consulta: Consulta, *, ventana_dias: int = VENTANA_DIAS) -> str:
    """URL del feed para una consulta.

    `when:Nd` es el operador de Google para acotar la antigüedad. Sin él, una
    consulta devuelve hasta 100 resultados de meses atrás en cada corrida, y el
    coste de inferencia se dispara reprocesando lo viejo.
    """
    terminos = f"{consulta.terminos} when:{ventana_dias}d"
    return PLANTILLA.format(consulta=quote(terminos, safe=""))
