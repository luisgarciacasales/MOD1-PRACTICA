"""Identificación léxica de tickers, entidades y sector (soporte de `validate`).

Responde la pregunta que decide entre Silver y cuarentena: *¿esta noticia es
identificable?* (PRD §6.2). **No es NER** — el NER real lo hace el LLM en la
etapa `enrich`, con contexto semántico y sobre las noticias que ya pasaron el
contrato. Aquí solo hay coincidencia de diccionario, deliberadamente barata:
la validación de 500 noticias debe caber en menos de 1 minuto (PRD §7).

Sesgo elegido: **generoso**. Un falso positivo manda a Silver una noticia poco
relevante, que el LLM descartará después. Un falso negativo la manda a
cuarentena y la pierde para siempre. El primero es reversible; el segundo no.
"""

from __future__ import annotations

import re
import unicodedata

from src.config.emisoras import ALIAS_EMISORAS, ENTIDADES_FINANCIERAS, LEXICO_SECTORES


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos, para comparar contra los alias."""
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


def _contiene(cuerpo: str, termino: str) -> bool:
    """Coincidencia con fronteras de palabra.

    Sin ellas, "amx" coincidiría dentro de "amxico" y "bmv" dentro de
    cualquier cadena que la contenga. `re.escape` es imprescindible porque
    varios alias traen apóstrofos y puntos ("domino's", "s&p global").
    """
    return re.search(rf"(?<!\w){re.escape(termino)}(?!\w)", cuerpo) is not None


def extraer_tickers(*textos: str) -> list[str]:
    """Emisoras del universo de Fase 1 mencionadas en el texto."""
    cuerpo = normalizar(" ".join(t for t in textos if t))
    encontrados = [
        ticker
        for ticker, alias in ALIAS_EMISORAS.items()
        if any(_contiene(cuerpo, a) for a in alias)
    ]
    return sorted(encontrados)


def extraer_entidades(*textos: str, fintechs: tuple[str, ...] = ()) -> list[str]:
    """Instituciones financieras y fintechs mencionadas.

    `fintechs` llega del diccionario Finnovista ya cargado, para no volver a
    leer el archivo por cada noticia.
    """
    cuerpo = normalizar(" ".join(t for t in textos if t))
    encontradas = {
        entidad for entidad in ENTIDADES_FINANCIERAS if _contiene(cuerpo, entidad)
    }
    encontradas |= {
        nombre for nombre in fintechs if _contiene(cuerpo, normalizar(nombre))
    }
    return sorted(encontradas)


def extraer_sector(*textos: str) -> str | None:
    """Sector afectado, si el léxico lo identifica. El primero que coincida.

    El orden de `LEXICO_SECTORES` importa: los sectores específicos
    (banca_consumo, pagos_digitales) van antes que los genéricos (banca,
    bursatil), porque un sector específico es el que permite resolver el proxy
    ticker aguas abajo y por tanto vale más.
    """
    cuerpo = normalizar(" ".join(t for t in textos if t))
    for sector, terminos in LEXICO_SECTORES.items():
        if any(_contiene(cuerpo, t) for t in terminos):
            return sector
    return None
