"""Calendario bursátil XMEX (PRD §6.6).

Resuelve el "siguiente día hábil de cotización", que es lo que hace correcta la
correlación noticia↔precio. Una noticia del viernes por la tarde no puede
medirse contra el precio del viernes —ya cerró— ni contra el del sábado —no
existe—: su impacto aparece el lunes, o el martes si el lunes es feriado
mexicano. Usar la fecha calendario produce falsos negativos sistemáticos.

El calendario se carga una vez y se consulta con búsqueda binaria: el JOIN de
500 noticias debe caber en 5 segundos (PRD §7) y llamar a pandas por cada fila
no lo permitiría.
"""

from __future__ import annotations

import bisect
from datetime import date, timedelta
from functools import lru_cache

# Bug encontrado el 25/26-ago-2026 al extender el histórico de precios de
# GFNORTEO.MX hasta 2000: con _DESDE fijo en 2020-01-01, cualquier noticia
# anterior a esa fecha caía fuera de `dias_habiles()` por completo —
# `bisect_right` sobre una lista que empieza en 2020 devuelve el índice 0
# para CUALQUIER fecha anterior, así que `siguiente_dia_habil` contestaba
# "2 de enero de 2020" para una noticia de 2018, sin error ni aviso. 25
# reportes históricos de Banorte (2018-2019) correlacionaron contra un
# precio de año y medio después antes de detectarse. _DESDE ahora cubre
# desde antes de que exista cualquier serie de precios plausible en el
# proyecto — más margen no cuesta nada (el calendario se construye una vez
# y se cachea) y evita que el mismo bug reaparezca la próxima vez que se
# extienda el histórico de otro ticker.
_DESDE = date(1990, 1, 1)
_HASTA = date(2030, 12, 31)


@lru_cache(maxsize=1)
def dias_habiles() -> tuple[date, ...]:
    """Días de cotización de la BMV, ordenados."""
    import pandas_market_calendars as mcal

    from src.config import get_settings

    calendario = mcal.get_calendar(get_settings().market_calendar)
    return tuple(d.date() for d in calendario.valid_days(_DESDE, _HASTA))


def es_dia_habil(f: date) -> bool:
    dias = dias_habiles()
    i = bisect.bisect_left(dias, f)
    return i < len(dias) and dias[i] == f


def siguiente_dia_habil(f: date) -> date | None:
    """Primer día de cotización **estrictamente posterior** a `f`.

    Estrictamente posterior, no "el mismo si es hábil": el criterio del PRD §8
    lo fija sin ambigüedad —"una noticia de viernes debe tener price_date =
    lunes"—. Medir el impacto en la sesión que ya estaba en curso cuando se
    publicó la noticia contaminaría el resultado con el movimiento previo.
    """
    dias = dias_habiles()
    i = bisect.bisect_right(dias, f)
    return dias[i] if i < len(dias) else None


def desplazar_habiles(f: date, n: int) -> date | None:
    """`n` días de cotización después de `f` (que debe ser hábil)."""
    dias = dias_habiles()
    i = bisect.bisect_left(dias, f)
    if i >= len(dias) or dias[i] != f:
        return None
    destino = i + n
    return dias[destino] if 0 <= destino < len(dias) else None


def dias_naturales_hasta_habil(f: date) -> int:
    """Cuántos días de calendario se saltan hasta el siguiente hábil.

    Sirve para diagnóstico: un valor de 3 delata un fin de semana, y uno mayor,
    un feriado mexicano.
    """
    siguiente = siguiente_dia_habil(f)
    return (siguiente - f).days if siguiente else 0


def rango_habil(desde: date, hasta: date) -> list[date]:
    dias = dias_habiles()
    i = bisect.bisect_left(dias, desde)
    j = bisect.bisect_right(dias, hasta)
    return list(dias[i:j])


__all__ = [
    "desplazar_habiles",
    "dias_habiles",
    "dias_naturales_hasta_habil",
    "es_dia_habil",
    "rango_habil",
    "siguiente_dia_habil",
    "timedelta",
]
