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

# Ventana amplia: cubre de sobra el histórico de 2 años y deja margen hacia
# adelante para el desplazamiento de 5 días hábiles.
_DESDE = date(2020, 1, 1)
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
