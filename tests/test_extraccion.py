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


# --- Términos del dominio financiero aportados por el usuario (2026-08-03) ---


@pytest.mark.parametrize(
    "texto,sector_esperado",
    [
        ("Aumenta el fraude bancario en cuentas digitales", "banca"),
        ("La CNBV endurece el marco regulatorio para la banca", "banca"),
        ("Las tasas de morosidad de la cartera crecen al 3%", "banca"),
        ("El costo de fondeo presiona el margen financiero", "banca"),
        ("Banxico inyecta liquidez bancaria al sistema", "banca"),
        ("La banca digital gana terreno frente a la sucursal", "banca_digital"),
        ("La hiperpersonalización redefine la experiencia del cliente", "banca_digital"),
        # Con contexto bancario explícito. "digitalización de procesos" a secas
        # NO califica: ver test_muletillas_de_negocio_no_son_banca_digital.
        ("La digitalización bancaria reduce costos operativos", "banca_digital"),
        ("Los neobancos captan usuarios jóvenes", "banca_digital"),
    ],
)
def test_lexico_financiero_del_dominio(texto, sector_esperado):
    assert extraer_sector(texto) == sector_esperado


def test_banca_digital_gana_al_sector_generico():
    """Una nota sobre digitalización bancaria debe ser banca_digital, no banca:
    solo el específico resuelve proxy ticker."""
    assert extraer_sector(
        "La digitalización de la banca múltiple avanza"
    ) == "banca_digital"


def test_captacion_pluvial_ya_no_es_captacion_bancaria():
    """Regresión de un falso positivo real: "la captación pluvial como pilar de
    infraestructura" se clasificaba como captacion_ahorro."""
    assert extraer_sector("La captación pluvial como pilar de infraestructura") is None
    assert extraer_sector("La captación bancaria creció 5%") == "captacion_ahorro"


def test_banca_digital_resuelve_proxy_ticker():
    """Es la frontera de competencia con los neobancos, así que debe poder
    traducirse a emisora."""
    from src.config.tickers import SECTOR_A_PROXY

    assert SECTOR_A_PROXY["banca_digital"] == ("RA.MX", "GFNORTEO.MX")


def test_politica_monetaria_de_fed_y_banxico_activa_el_bypass():
    """Los términos que el usuario pidió cubrir ya estaban en el léxico macro."""
    from src.contracts import es_macro

    assert es_macro("economista", "Banxico define su tasa objetivo tras la Fed") is True
    assert es_macro("economista", "El costo de fondeo sube con la política monetaria") is True


@pytest.mark.parametrize(
    "texto",
    [
        "Hacia la ley mexicana de la inteligencia artificial",   # decía transformación digital
        "¿Alguien está pensando en las green skills?",           # decía digitalización
        "Nace gigante legal por fusión de despachos",            # decía transformación digital
    ],
)
def test_muletillas_de_negocio_no_son_banca_digital(texto):
    """Regresión de falsos positivos reales: "digitalizacion" y "transformacion
    digital" a secas etiquetaban como banca digital notas de IA, sostenibilidad
    y derecho corporativo. Ahora todos los términos exigen contexto bancario."""
    assert extraer_sector(texto) != "banca_digital"


def test_banca_digital_sigue_reconociendo_lo_que_debe():
    for texto in ["La banca digital gana terreno", "Su app bancaria fue renovada",
                  "La digitalización bancaria acelera", "Los neobancos crecen",
                  "Apuestan por la hiperpersonalización de productos financieros"]:
        assert extraer_sector(texto) == "banca_digital", texto
