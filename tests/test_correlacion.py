"""Pruebas del calendario XMEX y de la selección de tickers para correlacionar.

El calendario es el mecanismo del que depende el criterio de correlación
temporal del PRD §8, y la selección de objetivos es la que decide si una
noticia sobre una fintech llega a medirse contra algún precio.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.pipeline.calendario import (
    desplazar_habiles,
    dias_naturales_hasta_habil,
    es_dia_habil,
    siguiente_dia_habil,
)
from src.pipeline.correlate import objetivos

FINTECHS = {"Nu", "Stori", "Ualá", "Klar"}


def fila(**kw):
    base = {
        "ner_tickers": [], "lex_tickers": [], "fintechs_identified": [],
        "lex_entities": [], "sector_affected": None, "lex_sector": None,
    }
    return base | kw


# --- Calendario XMEX --------------------------------------------------------


def test_viernes_salta_al_lunes():
    """Criterio literal del PRD §8."""
    viernes = date(2026, 7, 31)
    assert viernes.weekday() == 4
    siguiente = siguiente_dia_habil(viernes)
    assert siguiente == date(2026, 8, 3)
    assert siguiente.weekday() == 0


def test_el_fin_de_semana_no_cotiza():
    assert es_dia_habil(date(2026, 8, 1)) is False  # sábado
    assert es_dia_habil(date(2026, 8, 2)) is False  # domingo
    assert es_dia_habil(date(2026, 8, 3)) is True   # lunes


def test_es_estrictamente_posterior():
    """Un día hábil no es su propio 'siguiente': medir el impacto en la sesión
    que ya estaba en curso contaminaría el resultado."""
    lunes = date(2026, 8, 3)
    assert siguiente_dia_habil(lunes) != lunes
    assert siguiente_dia_habil(lunes) == date(2026, 8, 4)


def test_detecta_el_salto_de_fin_de_semana():
    assert dias_naturales_hasta_habil(date(2026, 7, 31)) == 3  # vie → lun
    assert dias_naturales_hasta_habil(date(2026, 8, 3)) == 1   # lun → mar


def test_feriado_mexicano_se_salta():
    """El 16 de septiembre (Independencia) no cotiza la BMV."""
    independencia = date(2026, 9, 16)
    assert es_dia_habil(independencia) is False
    assert siguiente_dia_habil(date(2026, 9, 15)) > independencia


def test_desplazamiento_de_5_sesiones_cruza_el_fin_de_semana():
    lunes = date(2026, 8, 3)
    quinto = desplazar_habiles(lunes, 5)
    assert quinto == date(2026, 8, 10)          # el lunes siguiente
    assert (quinto - lunes).days == 7           # 5 sesiones = 7 días naturales


def test_desplazar_desde_dia_no_habil_devuelve_none():
    assert desplazar_habiles(date(2026, 8, 1), 5) is None


# --- Selección de objetivos -------------------------------------------------


def test_ticker_directo_del_ner():
    r = objetivos(fila(ner_tickers=["GFNORTEO.MX"]), FINTECHS)
    assert [x["ticker"] for x in r] == ["GFNORTEO.MX"]
    assert r[0]["is_proxy"] is False
    assert r[0]["proxy_ticker"] is None


def test_une_ner_y_lexico():
    """El PRD §5.3 dice 'ticker detectado por NER o fuente'."""
    r = objetivos(fila(ner_tickers=["GFNORTEO.MX"], lex_tickers=["WALMEX.MX"]), FINTECHS)
    assert [x["ticker"] for x in r] == ["GFNORTEO.MX", "WALMEX.MX"]


def test_descarta_tickers_fuera_del_universo():
    r = objetivos(fila(lex_tickers=["AAPL", "GFNORTEO.MX"]), FINTECHS)
    assert [x["ticker"] for x in r] == ["GFNORTEO.MX"]


def test_proxy_cuando_la_fintech_no_cotiza():
    """Criterio del PRD §8: is_proxy = true con ticker proxy asignado."""
    r = objetivos(
        fila(fintechs_identified=["Nu"], sector_affected="banca_consumo"), FINTECHS
    )
    assert {x["ticker"] for x in r} == {"GFNORTEO.MX", "BBAJIOO.MX"}
    assert all(x["is_proxy"] for x in r)
    assert all(x["proxy_ticker"] == x["ticker"] for x in r)
    assert all(x["original_fintech"] == "Nu" for x in r)
    assert all(x["sector_affected"] == "banca_consumo" for x in r)


def test_el_proxy_sustituye_no_se_suma():
    """Si ya hay emisora directa, medir además su sector duplicaría la señal."""
    r = objetivos(
        fila(lex_tickers=["GFNORTEO.MX"], fintechs_identified=["Nu"],
             sector_affected="banca_consumo"),
        FINTECHS,
    )
    assert len(r) == 1
    assert r[0]["is_proxy"] is False


def test_fintech_detectada_por_el_lexico_de_silver():
    r = objetivos(fila(lex_entities=["Stori"], lex_sector="pagos_digitales"), FINTECHS)
    assert r and r[0]["is_proxy"] is True
    assert r[0]["original_fintech"] == "Stori"


def test_fintech_sin_sector_no_se_correlaciona():
    """Sin sector no hay forma honesta de asignarle un precio: mejor dejarla
    fuera que inventar una emisora."""
    assert objetivos(fila(fintechs_identified=["Nu"]), FINTECHS) == []


def test_sector_desconocido_no_produce_proxy():
    assert objetivos(
        fila(fintechs_identified=["Nu"], sector_affected="turismo"), FINTECHS
    ) == []


def test_noticia_sin_nada_no_se_correlaciona():
    assert objetivos(fila(), FINTECHS) == []


@pytest.mark.parametrize(
    "sector,esperados",
    [
        ("banca_consumo", {"GFNORTEO.MX", "BBAJIOO.MX"}),
        ("captacion_ahorro", {"GFNORTEO.MX"}),
        ("credito_automotriz", {"BBAJIOO.MX"}),
        ("pagos_digitales", {"GFNORTEO.MX"}),
        ("insurtech", {"WALMEX.MX"}),
    ],
)
def test_mapeo_sector_proxy_completo(sector, esperados):
    """Los cinco sectores de la tabla del PRD §3.3, con la serie accionaria
    corregida (ADR-11)."""
    r = objetivos(fila(fintechs_identified=["Nu"], sector_affected=sector), FINTECHS)
    assert {x["ticker"] for x in r} == esperados
