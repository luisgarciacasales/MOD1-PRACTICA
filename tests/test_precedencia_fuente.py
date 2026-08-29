"""El backfill desde PDF no puede ser efímero (29-ago-2026).

Durante un día, los fundamentales cargados desde los reportes oficiales de
Banorte se revertían solos: `validate --todo` revalida todo Bronze y los lotes
de `yahoo_fundamentals` volvían a escribir la fila. Como `verify` ejecuta
`validate --todo` por dentro, **cada verify deshacía el backfill**, y el valor
que tuviera la tabla dependía de cuándo se hubiera corrido cada comando.

Se comprobó en el servidor con un experimento directo sobre GFNORTEO 2025-03-31:

    backfill        → UPA 5.435   (lo que dice el reporte de la emisora)
    validate --todo → UPA 5.000   (lo que dice Yahoo, redondeado a entero)

Estos tests fijan las dos piezas que lo impiden: el campo `fuente` en el
contrato y la cláusula de precedencia en el UPSERT. El comportamiento
end-to-end exige PostgreSQL, así que aquí se verifica la ESTRUCTURA — que es
justamente lo que un refactor distraído borraría.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from src.contracts import validar_fundamental
from src.pipeline import db


def _crudo(**extra):
    return {"ticker": "GFNORTEO.MX", "period_end": date(2025, 3, 31),
            "utilidad_por_accion": 5.435, **extra}


def test_la_fuente_por_defecto_es_el_agregador():
    """Todo lo cargado hasta hoy vino de Yahoo, así que ese es el default y la
    migración 022 puede rellenar las filas existentes sin decidir nada."""
    fila = validar_fundamental(_crudo(), uuid4())
    assert fila.fuente == "yahoo"


def test_el_backfill_marca_el_reporte_oficial():
    fila = validar_fundamental(_crudo(fuente="reporte_pdf"), uuid4())
    assert fila.fuente == "reporte_pdf"


def test_una_fuente_inventada_va_a_cuarentena():
    """El campo es un Literal, no texto libre: una fuente nueva exige decidir
    explícitamente su lugar en la precedencia, no colarse por un typo."""
    resultado = validar_fundamental(_crudo(fuente="bloomberg"), uuid4())
    assert hasattr(resultado, "rejection_reason")


@pytest.mark.parametrize(
    "sql,tabla",
    [(db._SQL_FUNDAMENTALES, "silver_fundamentals"),
     (db._SQL_FUNDAMENTALES_ANUAL, "silver_fundamentals_anual")],
)
def test_el_upsert_protege_lo_que_vino_del_reporte(sql: str, tabla: str):
    """Sin este WHERE, un lote de Yahoo vuelve a pisar el dato del PDF y todo
    el backfill se pierde en el siguiente `verify`."""
    assert f"WHERE {tabla}.fuente <> 'reporte_pdf'" in sql
    assert "OR EXCLUDED.fuente = 'reporte_pdf'" in sql, (
        "el propio backfill debe poder reescribir sus filas; sin esta segunda "
        "condición, corregir un PDF mal extraído sería imposible"
    )


def test_preservada_no_se_cuenta_como_actualizada():
    """Con el WHERE, un UPSERT bloqueado no devuelve fila. Contarlo como
    actualización diría 'se escribió' de algo que no se escribió, que es
    exactamente el reporte engañoso que costó un día detectar."""
    carga = db.Carga(nuevas=1, actualizadas=2, preservadas=3)
    assert carga.total == 3
    otra = db.Carga(preservadas=1)
    carga += otra
    assert (carga.nuevas, carga.actualizadas, carga.preservadas) == (1, 2, 4)
