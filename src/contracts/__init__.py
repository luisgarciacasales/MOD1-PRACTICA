"""Contratos de datos Pydantic de la capa Silver.

Fuente única de verdad de la validación: las etapas del pipeline **importan**
estos modelos, no los redefinen (regla del skill `data-contracts`).

Cada validador devuelve o el modelo válido o un `DeadLetter` — nunca lanza y
nunca descarta en silencio. El llamador decide a qué tabla escribe.

    resultado = validar_noticia(crudo, batch_uuid)
    if isinstance(resultado, DeadLetter):
        ...  # → silver_dead_letters
    else:
        ...  # → silver_news
"""

from src.contracts.fintech import FintechDictEntry
from src.contracts.fundamentals import Fundamental, validar_fundamental
from src.contracts.market import (
    MacroIndicator,
    MarketPrice,
    validar_macro,
    validar_precio,
)
from src.contracts.news import (
    SilverNews,
    calcular_guid,
    es_macro,
    validar_noticia,
)
from src.contracts.rejections import DeadLetter, RejectionReason

__all__ = [
    "DeadLetter",
    "FintechDictEntry",
    "Fundamental",
    "MacroIndicator",
    "MarketPrice",
    "RejectionReason",
    "SilverNews",
    "calcular_guid",
    "es_macro",
    "validar_fundamental",
    "validar_macro",
    "validar_noticia",
    "validar_precio",
]
