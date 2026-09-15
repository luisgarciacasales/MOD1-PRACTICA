"""El calendario operativo y el oficial no pueden divergir sin que nos enteremos.

El pipeline decide si hay mercado con XMEX, vía `pandas_market_calendars`,
porque se actualiza con la librería en vez de a mano. Pero el calendario que
manda para el sector financiero mexicano es el que publica la CNBV, y son dos
cosas distintas: uno es el de la Bolsa Mexicana de Valores y otro el de las
entidades supervisadas.

Para 2026 coinciden en los diez días. Si algún año dejaran de hacerlo, sin esta
prueba la divergencia se descubriría el día que el pipeline corriera —o dejara
de correr— cuando no tocaba.
"""

from __future__ import annotations

import pytest

from src.config.calendario_cnbv import INHABILES_CNBV_2026
from src.pipeline.calendario import es_dia_habil


@pytest.mark.parametrize("fecha,motivo", INHABILES_CNBV_2026)
def test_xmex_tambien_considera_inhabil_cada_dia_de_la_cnbv(fecha, motivo: str):
    """Ninguno de los días que la CNBV declara inhábiles puede aparecer como
    hábil en el calendario que usa el pipeline."""
    assert not es_dia_habil(fecha), (
        f"XMEX considera hábil el {fecha} ({motivo}), que la CNBV declara inhábil"
    )


def test_la_lista_cubre_el_ano_completo():
    """Once días en 2026. Si alguien recorta la lista para 'limpiarla', el
    contraste dejaría de cubrir parte del año sin avisar."""
    assert len(INHABILES_CNBV_2026) == 11
    assert {f.year for f, _ in INHABILES_CNBV_2026} == {2026}


def test_un_dia_habil_normal_sigue_siendo_habil():
    """Control en la otra dirección: si el calendario se rompiera y devolviera
    inhábil siempre, las pruebas anteriores pasarían igual y el pipeline dejaría
    de correr en silencio."""
    from datetime import date

    assert es_dia_habil(date(2026, 9, 14))   # lunes ordinario
    assert es_dia_habil(date(2026, 6, 10))   # miércoles ordinario
