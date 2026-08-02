"""Utilidades de comparación de texto compartidas.

Viven aquí, y no en `contracts/` ni en `pipeline/`, porque **ambos** las
necesitan y deben comportarse igual. Tenerlas duplicadas ya costó un fallo: el
bypass macroeconómico comparaba por subcadena mientras la extracción léxica
comparaba por frontera de palabra, así que "fed" coincidía dentro de
"confederación" y noticias de deportes entraban a Silver como macro.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache


def normalizar(texto: str) -> str:
    """Minúsculas y sin acentos.

    Los medios mexicanos escriben "inflación" e "inflacion" indistintamente;
    comparar sin normalizar perdería la mitad de las coincidencias.
    """
    descompuesto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in descompuesto if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=2048)
def _patron(termino: str) -> re.Pattern[str]:
    # `re.escape` es imprescindible: hay términos con apóstrofo y ampersand
    # ("domino's", "s&p global") que romperían la expresión.
    return re.compile(rf"(?<!\w){re.escape(termino)}(?!\w)")


def contiene_termino(cuerpo_normalizado: str, termino: str) -> bool:
    """¿Aparece el término como palabra completa?

    Sin las fronteras, los términos cortos son minas: "fed" coincide dentro de
    "federación" y "confederación", "amx" dentro de "amxico", "pib" dentro de
    cualquier cadena que lo contenga.
    """
    return _patron(termino).search(cuerpo_normalizado) is not None


def terminos_presentes(cuerpo: str, terminos) -> set[str]:
    """Subconjunto de `terminos` que aparece como palabra completa en `cuerpo`."""
    normalizado = normalizar(cuerpo)
    return {t for t in terminos if contiene_termino(normalizado, t)}
