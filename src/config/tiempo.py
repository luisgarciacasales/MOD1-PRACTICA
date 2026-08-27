"""Zona horaria del mercado y fecha de jornada.

Existe para separar dos cosas que se confundían:

· **Instantes** (`ingested_at`, `rejected_at`, `updated_at`) van en **UTC**. Son
  marcas de cuándo ocurrió algo y deben ser comparables sin ambigüedad.
· **Etiquetas de jornada** —la carpeta `{YYYY-MM-DD}` de Bronze, el `hasta` que
  se le pide al SIE— van en **hora de Ciudad de México**. Responden a "¿de qué
  sesión bursátil es este lote?", y esa pregunta se contesta en la zona del
  mercado, no en la del reloj del contenedor.

El contenedor corre en UTC, así que a partir de las 18:00 CDMX su reloj ya está
en el día siguiente. Antes de esto, cualquier ingesta vespertina —que es la
normal, porque la BMV cierra a las 15:00— quedaba archivada bajo la fecha de
mañana. Ocurrió de verdad el 25-ago-2026 y otra vez el 26-ago.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

TZ_MERCADO = ZoneInfo("America/Mexico_City")


def ahora_mercado() -> datetime:
    """Instante actual en hora de mercado (consciente de zona horaria)."""
    return datetime.now(TZ_MERCADO)


def hoy_mercado() -> date:
    """Fecha de jornada actual en Ciudad de México.

    Es la que etiqueta los lotes de Bronze. Nunca uses `datetime.now(UTC).date()`
    para eso: de 18:00 CDMX en adelante devuelve el día siguiente.
    """
    return ahora_mercado().date()
