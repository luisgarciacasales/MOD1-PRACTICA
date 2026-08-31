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


CAMPOS_FINANCIEROS = (
    "ingresos_totales", "utilidad_neta", "utilidad_por_accion",
    "activo_total", "pasivo_total", "capital_contable",
    "acciones_en_circulacion", "flujo_operativo", "flujo_libre",
)


@pytest.mark.parametrize(
    "sql,tabla",
    [(db._SQL_FUNDAMENTALES, "silver_fundamentals"),
     (db._SQL_FUNDAMENTALES_ANUAL, "silver_fundamentals_anual")],
)
@pytest.mark.parametrize("campo", CAMPOS_FINANCIEROS)
def test_la_precedencia_se_aplica_campo_a_campo(sql: str, tabla: str, campo: str):
    """Cada campo decide por separado quién gana.

    La primera versión protegía la FILA entera y por eso destruía datos: el PDF
    de Banorte aporta tres campos y Yahoo nueve, así que al ganar la fila se
    llevaba por delante acciones en circulación, activo total y flujos — y con
    las acciones, el P/VL trimestral de esos periodos. Si alguien vuelve a
    escribir un `EXCLUDED.<campo>` pelado, este test lo caza.
    """
    assert f"COALESCE(EXCLUDED.{campo}, {tabla}.{campo})" in sql, (
        "entra el reporte: debe aportar lo suyo y respetar el resto"
    )
    assert f"COALESCE({tabla}.{campo}, EXCLUDED.{campo})" in sql, (
        "entra el agregador sobre un reporte: solo debe rellenar huecos"
    )


@pytest.mark.parametrize(
    "sql,tabla",
    [(db._SQL_FUNDAMENTALES, "silver_fundamentals"),
     (db._SQL_FUNDAMENTALES_ANUAL, "silver_fundamentals_anual")],
)
def test_una_fuente_refrescandose_a_si_misma_puede_borrar(sql: str, tabla: str):
    """Sin esta rama, un campo que desapareció del origen quedaría fosilizado:
    el COALESCE lo conservaría para siempre y nadie podría corregirlo."""
    assert f"WHEN EXCLUDED.fuente = {tabla}.fuente THEN EXCLUDED." in sql


@pytest.mark.parametrize(
    "sql,tabla",
    [(db._SQL_FUNDAMENTALES, "silver_fundamentals"),
     (db._SQL_FUNDAMENTALES_ANUAL, "silver_fundamentals_anual")],
)
def test_la_etiqueta_de_fuente_no_degrada(sql: str, tabla: str):
    """Una fila que ya lleva algo del reporte oficial sigue etiquetada así
    aunque el agregador le rellene huecos: si se degradara a `yahoo`, la
    siguiente carga podría pisar los campos del reporte."""
    assert f"WHEN 'reporte_pdf' IN (EXCLUDED.fuente, {tabla}.fuente)" in sql


def test_ingresos_negativos_van_a_cuarentena():
    """Yahoo deriva el trimestre restando periodos y a veces sale negativo
    (GFNORTEO 2025-06-30: ingresos −13,555 mdp). Unas ventas negativas no son
    un dato observado, y dejarlas pasar daba un ROE trimestral de −1.1% para
    un banco que ese trimestre ganó ~14,600 mdp."""
    resultado = validar_fundamental(_crudo(ingresos_totales=-13_555_361_681.0), uuid4())
    assert hasattr(resultado, "rejection_reason")


def test_una_perdida_real_si_pasa():
    """La restricción es sobre INGRESOS, no sobre el resultado: una emisora
    puede perder dinero y eso es un dato legítimo que no se puede censurar."""
    fila = validar_fundamental(
        _crudo(ingresos_totales=49_851_000_000.0, utilidad_neta=-2_000_000_000.0), uuid4()
    )
    assert fila.utilidad_neta == -2_000_000_000.0


# --- Los reportes en PDF son una fuente de Bronze, no un atajo a Silver ------
#
# Hasta el 31-ago-2026 el backfill escribía directo a Silver, y eso dejaba los
# reportes oficiales fuera de la cadena que hace reproducible el pipeline:
# `validate` regenera Silver desde Bronze, así que una base reconstruida perdía
# los 28 trimestres del PDF hasta que alguien se acordara de reejecutar el
# backfill a mano.


def test_la_fuente_del_lote_es_la_que_validate_reconoce():
    """Si estos dos nombres se separan, los lotes de reportes entran a Bronze y
    `validate` los ignora en silencio — ni cargados ni en cuarentena."""
    from src.pipeline.backfill_fundamentales import FUENTE
    from src.pipeline.validate import FUENTE_REPORTES_PDF

    assert FUENTE == FUENTE_REPORTES_PDF == "reportes_pdf"


def test_la_precedencia_se_deriva_del_lote_no_del_registro():
    """El payload de Bronze NO lleva un campo `fuente`: es un dato sobre el
    dato, editable y redundante con el `source` del propio lote. `validate` lo
    deriva al validar, que es lo que reproduce este test."""
    from src.pipeline.backfill_fundamentales import FUENTE

    crudo_en_bronze = {"ticker": "GFNORTEO.MX", "period_end": "2025-03-31",
                       "utilidad_por_accion": 5.435, "source": FUENTE}
    assert "fuente" not in crudo_en_bronze

    fila = validar_fundamental(
        {k: v for k, v in crudo_en_bronze.items() if k != "source"} | {"fuente": "reporte_pdf"},
        uuid4(), source=FUENTE,
    )
    assert fila.fuente == "reporte_pdf"


def test_una_fila_sin_UPA_ya_no_se_descarta():
    """3T24 de Banorte tiene capital contable y utilidad neta perfectamente
    extraíbles, y se tiraba entero por no encontrar la UPA (el texto del PDF
    sale con espacios espurios: `4.91 1` en vez de `4.911`). Desde que existe
    el ROE —que no necesita UPA— esa fila vale."""
    fila = validar_fundamental(
        {"ticker": "GFNORTEO.MX", "period_end": date(2024, 9, 30),
         "utilidad_neta": 14_238_000_000.0, "capital_contable": 253_186_000_000.0,
         "fuente": "reporte_pdf"},
        uuid4(),
    )
    assert fila.utilidad_por_accion is None
    assert 100.0 * fila.utilidad_neta * 4 / fila.capital_contable == pytest.approx(22.5, abs=0.1)


# --- Bronze es acumulativo, así que el ORDEN de aplicación importa -----------


def test_los_lotes_se_aplican_en_orden_cronologico(tmp_path):
    """El directorio de un lote termina en su UUID, así que ordenar por ruta es
    ordenar al azar. Con Bronze acumulativo y UPSERT, el último aplicado gana:
    un lote que corrige a otro puede quedar revertido por él.

    Pasó de verdad el 31-ago-2026 con tres lotes de reportes_pdf del mismo día
    — ganó el defectuoso porque su UUID empezaba por `f` y el bueno por `b`.
    """
    from src.pipeline.bronze import escribir_lote, listar_lotes

    escritos = [
        escribir_lote([{"n": i}], source="reportes_pdf", categoria="fundamentals",
                      fecha=date(2026, 8, 31), raiz_bronze=tmp_path)
        for i in range(6)
    ]
    listados = listar_lotes(tmp_path)

    assert [l.ruta for l in escritos] == listados, (
        "el orden de listado debe ser el de escritura, no el alfabético del UUID"
    )
    # Y que el caso patológico esté realmente cubierto: si los UUID salieran
    # ya ordenados, el test pasaría sin comprobar nada.
    assert sorted(listados) != listados or len({l.ruta.name[0] for l in escritos}) == 1
