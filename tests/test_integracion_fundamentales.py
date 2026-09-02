"""Pruebas de integración del UPSERT de fundamentales, contra PostgreSQL real.

Cada una corresponde a un defecto que estuvo en producción. La batería
anterior comprobaba que el SQL *contuviera* ciertos textos; estas comprueban
que la base *se comporte*, que es lo que fallaba.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from src.contracts import validar_fundamental
from src.pipeline import db

TICKER = "GFNORTEO.MX"
PERIODO = date(2025, 3, 31)


def _fila(fuente: str, **campos):
    return validar_fundamental(
        {"ticker": TICKER, "period_end": PERIODO, "fuente": fuente, **campos}, uuid4()
    )


def _leer(cur) -> dict:
    cur.execute(
        "SELECT fuente, utilidad_por_accion, utilidad_neta, capital_contable, "
        "       acciones_en_circulacion, activo_total "
        "FROM silver_fundamentals WHERE ticker = %s AND period_end = %s",
        (TICKER, PERIODO),
    )
    fila = cur.fetchone()
    if fila is None:
        return {}
    return dict(zip(
        ("fuente", "upa", "neta", "capital", "acciones", "activo"), fila, strict=True
    ))


def test_el_agregador_no_pisa_el_reporte_pero_rellena_sus_huecos(cur):
    """El defecto del 29-ago: `validate --todo` devolvía la UPA del reporte
    (5.435) al valor redondeado de Yahoo (5.0), y como `verify` corre
    `validate --todo` por dentro, cada verify deshacía el backfill.

    El del 31-ago es el complementario: al protegerse la FILA entera, el
    reporte se llevaba por delante los campos que él no trae y Yahoo sí.
    Ambos se comprueban aquí, porque el arreglo de uno provocó el otro.
    """
    db.cargar_fundamentales(cur, [_fila("reporte_pdf", utilidad_por_accion=5.435,
                                        capital_contable=266_135e6)])
    db.cargar_fundamentales(cur, [_fila("yahoo", utilidad_por_accion=5.0,
                                        capital_contable=267_056e6,
                                        acciones_en_circulacion=2_812_156_594.0,
                                        activo_total=2_552_759e6)])

    estado = _leer(cur)
    assert estado["upa"] == 5.435, "el agregador pisó el dato del reporte"
    assert estado["capital"] == 266_135e6, "el agregador pisó el capital del reporte"
    assert estado["acciones"] == 2_812_156_594.0, (
        "el reporte no trae acciones: debió conservarse el dato del agregador"
    )
    assert estado["activo"] == 2_552_759e6
    assert estado["fuente"] == "reporte_pdf", "la etiqueta de fuente se degradó"


def test_el_reporte_manda_llegue_cuando_llegue(cur):
    """Mismo resultado con el orden inverso: la precedencia no puede depender
    de quién cargó primero, que era justo el problema — el valor de la tabla
    cambiaba según en qué orden se hubieran corrido los comandos."""
    db.cargar_fundamentales(cur, [_fila("yahoo", utilidad_por_accion=5.0,
                                        acciones_en_circulacion=2_812_156_594.0)])
    db.cargar_fundamentales(cur, [_fila("reporte_pdf", utilidad_por_accion=5.435)])

    estado = _leer(cur)
    assert estado["upa"] == 5.435
    assert estado["acciones"] == 2_812_156_594.0


def test_una_fuente_puede_corregirse_a_si_misma(cur):
    """Sin esta rama, un campo mal extraído quedaría fosilizado por el COALESCE
    y no habría forma de retirarlo. Es el caso del 1T26, donde la utilidad neta
    venía de otra entidad del grupo y hubo que descartarla."""
    db.cargar_fundamentales(cur, [_fila("reporte_pdf", utilidad_por_accion=5.495,
                                        utilidad_neta=11_912e6)])
    db.cargar_fundamentales(cur, [_fila("reporte_pdf", utilidad_por_accion=5.495)])

    assert _leer(cur)["neta"] is None, "el campo retirado quedó fosilizado"


def test_reprocesar_no_crea_filas(cur):
    """Criterio §8 del PRD, comprobado sobre la base y no sobre el texto del
    SQL: la segunda carga del mismo lote no puede insertar nada."""
    fila = _fila("yahoo", utilidad_por_accion=5.0)
    primera = db.cargar_fundamentales(cur, [fila])
    segunda = db.cargar_fundamentales(cur, [fila])

    assert (primera.nuevas, primera.actualizadas) == (1, 0)
    assert (segunda.nuevas, segunda.actualizadas) == (0, 1)


def test_el_contrato_rechaza_ingresos_negativos_antes_de_la_base(cur):
    """Yahoo deriva trimestres restando periodos y a veces salen negativos.
    La fila no debe llegar a Silver: se comprueba que efectivamente no está."""
    resultado = validar_fundamental(
        {"ticker": TICKER, "period_end": PERIODO, "ingresos_totales": -13_555e6}, uuid4()
    )
    assert hasattr(resultado, "rejection_reason")
    assert _leer(cur) == {}


@pytest.mark.parametrize("fuente", ["yahoo", "reporte_pdf"])
def test_la_carga_es_transaccional_por_fuente(cur, fuente):
    """Comprobación de fontanería: que el rollback del fixture aísle de verdad
    una prueba de otra. Si esto falla, los demás resultados no valen nada."""
    assert _leer(cur) == {}, "una prueba anterior dejó estado en la base"
    db.cargar_fundamentales(cur, [_fila(fuente, utilidad_por_accion=1.0)])
    assert _leer(cur)["fuente"] == fuente
