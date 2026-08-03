"""Pruebas de la identificación léxica y de la normalización Bronze→contrato."""

from __future__ import annotations

from datetime import date

import pytest

from src.pipeline.extraccion import extraer_entidades, extraer_sector, extraer_tickers
from src.pipeline.validate import normalizar_macro, normalizar_noticia


# --- Tickers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("Banorte reporta utilidades récord", ["GFNORTEO.MX"]),
        ("Walmart de México abre 50 tiendas", ["WALMEX.MX"]),
        ("América Móvil y Grupo México lideran", ["AMXB.MX", "GMEXICOB.MX"]),
        ("Oxxo, propiedad de FEMSA, crece", ["FEMSAUBD.MX"]),
        ("El clima estará soleado en Monterrey", []),
    ],
)
def test_extrae_tickers_por_alias(texto, esperado):
    assert extraer_tickers(texto) == esperado


def test_alias_respeta_fronteras_de_palabra():
    """Sin fronteras, 'amx' coincidiría dentro de otras palabras."""
    assert extraer_tickers("la palabra amxico no es una emisora") == []
    assert extraer_tickers("AMX subió 2%") == ["AMXB.MX"]


def test_alias_ignora_acentos_y_mayusculas():
    assert extraer_tickers("AMÉRICA MÓVIL") == extraer_tickers("america movil")


def test_alias_con_caracteres_especiales_no_rompe_la_regex():
    """'domino's' y 's&p global' llevan caracteres que hay que escapar."""
    assert extraer_tickers("Domino's abre sucursales") == ["ALSEA.MX"]
    assert "s&p global" in extraer_entidades("S&P Global revisó la nota")


# --- Entidades y sector ----------------------------------------------------


def test_extrae_entidades_financieras():
    """Instituciones sin ticker en el universo: se detectan como entidad."""
    assert "citibanamex" in extraer_entidades("Citibanamex reportó su cartera")
    assert "banxico" in extraer_entidades("Banxico publicó su informe trimestral")


def test_los_bancos_con_ticker_se_detectan_como_emisora_no_como_entidad():
    """Tras la ampliación del 2026-08-03, BBVA y Santander tienen ticker (sus
    matrices vía SIC), así que dejan de ser 'entidad sin cotización'."""
    assert extraer_tickers("BBVA México reportó su cartera") == ["BBVA.MX"]
    assert extraer_tickers("Santander México eleva su guía") == ["SANN.MX"]
    assert extraer_entidades("BBVA México reportó su cartera") == []


def test_reconoce_fintechs_del_diccionario():
    """Las fintechs conservan su nombre tal como está en el diccionario."""
    entidades = extraer_entidades("Nu y Stori compiten", fintechs=("Nu", "Stori", "Klar"))
    assert set(entidades) == {"Nu", "Stori"}


def test_sector_especifico_gana_al_generico():
    """banca_consumo permite resolver el proxy ticker; banca genérica no."""
    assert extraer_sector("crecen los créditos al consumo en la banca múltiple") == "banca_consumo"


def test_sin_senal_no_inventa_sector():
    assert extraer_sector("el equipo ganó el partido") is None


# --- Normalización de noticias ---------------------------------------------


def test_normaliza_entrada_rss_tipica():
    crudo = {
        "source": "financiero",
        "title": "Banorte eleva su guía de utilidades",
        "summary": "El banco anticipa mayor crédito al consumo este trimestre.",
        "link": "https://www.elfinanciero.com.mx/mercados/banorte",
        "published_parsed": "2026-08-01T14:00:00Z",
    }
    datos = normalizar_noticia(crudo, fintechs=())
    assert datos["tickers"] == ["GFNORTEO.MX"]
    assert datos["sector"] == "banca_consumo"
    assert datos["url"].startswith("https://")


def test_usa_el_texto_mas_largo_disponible():
    crudo = {
        "source": "bloomberg",
        "title": "t",
        "summary": "corto",
        "content": [{"value": "un cuerpo bastante más largo que el resumen"}],
        "link": "https://x.mx/a",
        "published_parsed": "2026-08-01T14:00:00Z",
    }
    assert "bastante más largo" in normalizar_noticia(crudo, fintechs=())["content"]


def test_sin_cuerpo_el_titular_hace_de_contenido():
    """El contrato exige content no vacío; descartar por eso perdería una
    noticia que sí es identificable."""
    crudo = {
        "source": "financiero",
        "title": "Cemex anuncia inversión",
        "link": "https://x.mx/a",
        "published_parsed": "2026-08-01T14:00:00Z",
    }
    datos = normalizar_noticia(crudo, fintechs=())
    assert datos["content"] == "Cemex anuncia inversión"
    assert datos["tickers"] == ["CEMEXCPO.MX"]


# --- Normalización de series BANXICO ---------------------------------------


def test_normaliza_serie_banxico():
    datos = normalizar_macro({"series_id": "SF43783", "fecha": "31/07/2026", "dato": "6.7559"})
    assert datos == {"series_id": "SF43783", "date": date(2026, 7, 31), "value": 6.7559}


def test_valor_con_separador_de_miles():
    datos = normalizar_macro({"series_id": "SF617", "fecha": "30/06/2026", "dato": "3,412.55"})
    assert datos["value"] == 3412.55


@pytest.mark.parametrize("dato", ["N/E", "", None, "s/d"])
def test_dato_no_disponible_no_se_convierte_en_cero(dato):
    """Convertir 'N/E' en 0.0 mentiría sobre el estado de la economía."""
    assert normalizar_macro({"series_id": "SF43783", "fecha": "31/07/2026", "dato": dato}) is None


def test_fecha_malformada_se_rechaza():
    assert normalizar_macro({"series_id": "SF43783", "fecha": "2026-07-31", "dato": "6.75"}) is None


# --- Limpieza de HTML y acotado (límites del contrato) ---------------------


def test_quita_el_markup_del_cuerpo():
    from src.pipeline.validate import texto_plano

    html = "<p>Banorte <strong>eleva</strong> su guía.</p><script>x=1</script>"
    plano = texto_plano(html)
    assert "<" not in plano
    assert "Banorte" in plano and "eleva" in plano


def test_texto_sin_markup_pasa_intacto():
    from src.pipeline.validate import texto_plano

    assert texto_plano("texto sin etiquetas") == "texto sin etiquetas"


def test_acotar_respeta_fronteras_de_palabra():
    from src.pipeline.validate import acotar

    recortado = acotar("palabra " * 100, 50)
    assert len(recortado) <= 50
    assert recortado.endswith("…")
    assert not recortado[:-1].rstrip().endswith("palabr")  # no parte a media palabra


def test_acotar_no_toca_lo_que_ya_cabe():
    from src.pipeline.validate import acotar

    assert acotar("corto", 8192) == "corto"


def test_articulo_html_largo_ya_no_va_a_cuarentena():
    """Regresión: 330 noticias se rechazaban por longitud del HTML crudo."""
    from uuid import uuid4

    from src.contracts import SilverNews, validar_noticia
    from src.pipeline.validate import LIMITE_CONTENIDO, normalizar_noticia

    crudo = {
        "source": "bloomberg",
        "title": "Cemex anuncia inversión en México",
        "content": [{"value": "<p>" + ("La emisora informó que. " * 1200) + "</p>"}],
        "link": "https://www.bloomberglinea.com/nota",
        "published_parsed": "2026-08-01T14:00:00Z",
    }
    datos = normalizar_noticia(crudo, fintechs=())
    assert len(datos["content"]) <= LIMITE_CONTENIDO
    assert isinstance(validar_noticia(datos, uuid4()), SilverNews)
