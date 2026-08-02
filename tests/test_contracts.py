"""Pruebas de los contratos Silver.

Cubren las reglas que el skill `data-contracts` declara verificables y los
criterios de aceptación del PRD §8 que dependen del contrato.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.contracts import (
    DeadLetter,
    FintechDictEntry,
    MacroIndicator,
    MarketPrice,
    RejectionReason,
    SilverNews,
    calcular_guid,
    es_macro,
    validar_macro,
    validar_noticia,
    validar_precio,
)

BATCH = uuid4()


def noticia(**overrides) -> dict:
    base = {
        "source": "economista",
        "title": "Grupo Financiero Banorte reporta utilidades récord",
        "content": "La emisora informó un incremento de 12% en su utilidad neta.",
        "url": "https://www.eleconomista.com.mx/mercados/banorte-utilidades",
        "published_at": "2026-07-31T21:30:00+00:00",
        "tickers": ["GFNORTE"],
    }
    return base | overrides


# --- Tipado estricto -------------------------------------------------------


def test_noticia_valida_pasa():
    r = validar_noticia(noticia(), BATCH)
    assert isinstance(r, SilverNews)
    assert r.tickers == ["GFNORTE"]
    assert r.enriched is False
    assert r.macro_bypass is False


def test_title_demasiado_largo_va_a_cuarentena():
    r = validar_noticia(noticia(title="x" * 1025), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.TYPE_MISMATCH
    assert "title" in (r.rejection_detail or "")


def test_content_demasiado_largo_va_a_cuarentena():
    r = validar_noticia(noticia(content="x" * 8193), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.TYPE_MISMATCH


@pytest.mark.parametrize("url", ["", "   ", "no-es-una-url", "ftp:/roto"])
def test_url_invalida_va_a_cuarentena(url):
    r = validar_noticia(noticia(url=url), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.INVALID_URL


@pytest.mark.parametrize("fecha", ["", "ayer", "31/07/2026", None])
def test_fecha_invalida_va_a_cuarentena(fecha):
    r = validar_noticia(noticia(published_at=fecha), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.INVALID_DATE


def test_source_desconocida_va_a_cuarentena():
    r = validar_noticia(noticia(source="reforma"), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.UNKNOWN_SOURCE


# --- Integridad semántica (PRD §8: "MISSING_ENTITY") -----------------------


def test_sin_entidad_ninguna_es_missing_entity():
    r = validar_noticia(
        noticia(
            title="Reporte del sector",
            content="Sin detalles adicionales.",
            tickers=None,
        ),
        BATCH,
    )
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.MISSING_ENTITY
    # El payload original debe conservarse para poder revisarlo a mano.
    assert r.raw_payload["title"] == "Reporte del sector"
    assert r.batch_uuid == BATCH


@pytest.mark.parametrize(
    "campo,valor",
    [("tickers", ["WALMEX"]), ("sector", "banca"), ("entities", ["Carlos Slim"])],
)
def test_basta_una_de_las_tres_senales(campo, valor):
    datos = noticia(tickers=None) | {campo: valor}
    assert isinstance(validar_noticia(datos, BATCH), SilverNews)


def test_listas_vacias_no_cuentan_como_entidad():
    r = validar_noticia(noticia(tickers=[], entities=[]), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.MISSING_ENTITY


# --- Bypass macroeconómico (PRD §8) ----------------------------------------


def test_macro_sin_ticker_pasa_con_bypass():
    """Criterio del §8: ≥1 noticia macro sin ticker en silver_news."""
    r = validar_noticia(
        noticia(
            source="economista",
            title="Banxico mantiene la tasa objetivo en 7.25%",
            content=(
                "La Junta de Gobierno del Banco de México decidió mantener sin "
                "cambio la tasa de referencia, ante una inflación subyacente "
                "que cede más lento de lo previsto."
            ),
            tickers=None,
        ),
        BATCH,
    )
    assert isinstance(r, SilverNews)
    assert r.macro_bypass is True
    assert r.tickers is None


def test_bloomberg_sin_lexico_macro_NO_activa_bypass():
    """Endurecimiento (ADR-10): la fuente sola no es un pase libre.

    El PRD leído al pie de la letra dejaría pasar cualquier huérfana de
    Bloomberg. Aquí se exige que el texto hable de macro.
    """
    r = validar_noticia(
        noticia(
            source="bloomberg",
            title="Nueva cafetería abre en Polanco",
            content="El local ofrece grano de Chiapas.",
            tickers=None,
        ),
        BATCH,
    )
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.MISSING_ENTITY


def test_bloomberg_refuerza_con_un_solo_termino():
    """La fuente sí baja el umbral de 2 términos a 1: es señal reforzadora."""
    texto = "El banco central discutió el rumbo de la política monetaria."
    assert es_macro("bloomberg", texto) is True
    assert es_macro("economista", texto) is False


def test_una_sola_mencion_macro_no_basta():
    """El umbral evita que cualquier huérfana se cuele por decir 'inflación'."""
    assert es_macro("economista", "La inflación de costos afectó al restaurante") is False


def test_noticia_con_ticker_no_marca_bypass_falsamente():
    r = validar_noticia(noticia(), BATCH)
    assert isinstance(r, SilverNews)
    assert r.macro_bypass is False


# --- Clave natural e idempotencia (PRD §8: 0 duplicados) -------------------


def test_guid_es_estable():
    a = validar_noticia(noticia(), BATCH)
    b = validar_noticia(noticia(), uuid4())  # otro batch, mismo artículo
    assert isinstance(a, SilverNews) and isinstance(b, SilverNews)
    assert a.guid == b.guid, "el mismo artículo debe producir el mismo guid"


def test_guid_ignora_la_zona_horaria():
    """Mismo instante en dos husos ⇒ mismo guid, o la idempotencia se rompe."""
    utc = calcular_guid("economista", "https://x.mx/a", datetime(2026, 7, 31, 21, 30, tzinfo=UTC))
    from datetime import timedelta, timezone

    cdmx = calcular_guid(
        "economista",
        "https://x.mx/a",
        datetime(2026, 7, 31, 15, 30, tzinfo=timezone(timedelta(hours=-6))),
    )
    assert utc == cdmx


def test_guid_cambia_con_la_url():
    a = validar_noticia(noticia(), BATCH)
    b = validar_noticia(noticia(url="https://www.eleconomista.com.mx/otra"), BATCH)
    assert isinstance(a, SilverNews) and isinstance(b, SilverNews)
    assert a.guid != b.guid


def test_tickers_se_normalizan_y_deduplican():
    r = validar_noticia(noticia(tickers=[" gfnorte ", "GFNORTE", "walmex"]), BATCH)
    assert isinstance(r, SilverNews)
    assert r.tickers == ["GFNORTE", "WALMEX"]


def test_fecha_naive_se_asume_utc():
    r = validar_noticia(noticia(published_at="2026-07-31T21:30:00"), BATCH)
    assert isinstance(r, SilverNews)
    assert r.published_at.tzinfo is not None


# --- Datos de mercado ------------------------------------------------------


def precio(**overrides) -> dict:
    base = {
        "ticker": "gfnorte.mx",
        "date": "2026-07-31",
        "open": 168.0,
        "high": 171.5,
        "low": 167.2,
        "close": 170.9,
        "adj_close": 170.9,
        "volume": 4_120_000,
    }
    return base | overrides


def test_precio_valido_pasa_y_normaliza_ticker():
    r = validar_precio(precio(), BATCH)
    assert isinstance(r, MarketPrice)
    assert r.ticker == "GFNORTE.MX"


@pytest.mark.parametrize("campo", ["open", "high", "low", "close", "adj_close"])
def test_precio_no_positivo_es_out_of_range(campo):
    r = validar_precio(precio(**{campo: 0.0}), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.OUT_OF_RANGE


def test_volumen_negativo_es_out_of_range():
    r = validar_precio(precio(volume=-1), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.OUT_OF_RANGE


def test_ohlc_incoherente_se_rechaza():
    """low > high pasa cualquier 'precio > 0' y aun así es basura."""
    r = validar_precio(precio(low=200.0, high=150.0), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.OUT_OF_RANGE


def test_close_fuera_del_rango_del_dia_se_rechaza():
    r = validar_precio(precio(close=999.0), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.OUT_OF_RANGE


def test_dead_letter_de_precio_es_serializable_a_jsonb():
    from datetime import date as _date

    r = validar_precio(precio(date=_date(2026, 7, 31), close=999.0), BATCH)
    assert isinstance(r, DeadLetter)
    assert r.raw_payload["date"] == "2026-07-31"  # ISO, no objeto date


def test_macro_admite_valores_negativos():
    r = validar_macro({"series_id": "SF10770", "date": "2026-06-30", "value": -0.12}, BATCH)
    assert isinstance(r, MacroIndicator)
    assert r.value == -0.12


def test_macro_sin_campo_obligatorio():
    r = validar_macro({"series_id": "SF43783"}, BATCH)
    assert isinstance(r, DeadLetter)
    assert r.rejection_reason is RejectionReason.MISSING_FIELD


# --- Diccionario Finnovista ------------------------------------------------


def test_fintech_sin_ticker_necesita_proxy():
    nu = FintechDictEntry(
        legal_name="Nu México Financiera, S.A. de C.V., S.F.P.",
        commercial_name="Nu",
        sector="neobanco",
    )
    assert nu.cotiza_en_bmv is False


def test_fintech_ticker_vacio_se_normaliza_a_none():
    e = FintechDictEntry(
        legal_name="Klar Technologies", commercial_name="Klar", ticker="  ", sector="lending"
    )
    assert e.ticker is None
