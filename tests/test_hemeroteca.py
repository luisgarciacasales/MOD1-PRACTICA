"""El filtro que hace viable recuperar cinco años de archivo.

De 755 artículos archivados de `/mercados/` en marzo de 2020, solo 125
mencionaban una emisora del universo. Ese 17% es la diferencia entre siete
horas de descargas y cuarenta: todo lo que estas pruebas protegen es la
capacidad de decidir qué NO pedirle al Internet Archive.
"""

from __future__ import annotations

import pytest

from src.config.emisoras import ALIAS_EMISORAS
from src.config.hemeroteca import FUENTES_ARCHIVO, MEDIOS
from src.sources.hemeroteca import _titular_de_url, menciona_emisora

PATRON = r"-(\d{8})-\d+\.html"
BASE = "https://www.eleconomista.com.mx/mercados/"


@pytest.mark.parametrize("slug,esperado", [
    ("BMV-pierde-mas-de-2-ante-temores-al-coronavirus-FEMSA-encabeza-la-caida-20200309-0021.html",
     "BMV pierde mas de 2 ante temores al coronavirus FEMSA encabeza la caida"),
    ("Alsea-pierde-2334-millones-en-un-dia-20200324-0007.html",
     "Alsea pierde 2334 millones en un dia"),
])
def test_el_titular_sale_de_la_url(slug: str, esperado: str):
    """Sin descargar nada. Es lo que permite filtrar antes de pedir."""
    assert _titular_de_url(BASE + slug, PATRON) == esperado


@pytest.mark.parametrize("titular,ticker", [
    ("BMV y Biva en rojo acciones de Banorte caen 10", "GFNORTEO.MX"),
    ("BMV pierde mas de 2 ante temores al coronavirus FEMSA encabeza la caida", "FEMSAUBD.MX"),
    ("Alsea pierde 2334 millones en un dia", "ALSEA.MX"),
])
def test_reconoce_la_emisora_del_titular(titular: str, ticker: str):
    assert ticker in menciona_emisora(titular, ALIAS_EMISORAS)


@pytest.mark.parametrize("titular", [
    "Wall Street cierra mixto tras datos de empleo",
    "El petroleo sube 3 por recorte de la OPEP",
])
def test_descarta_lo_que_no_menciona_emisoras(titular: str):
    """Cada falso positivo aquí es una descarga inútil al Archive, y cada uno
    de esos segundos sale del presupuesto de un servicio público gratuito."""
    assert menciona_emisora(titular, ALIAS_EMISORAS) == []


def test_no_casa_con_fragmentos_de_dos_o_tres_letras():
    """El cedazo exige alias de cuatro caracteres o más. Con menos, una `q` o
    un `ra` sueltos arrastrarían medio archivo — el mismo error de subcadenas
    que ADR-17 tuvo que deshacer en las correlaciones."""
    assert menciona_emisora("La q y el ra son letras sueltas aqui", ALIAS_EMISORAS) == []


def test_las_fuentes_de_archivo_estan_registradas_en_validate():
    """Si no lo estuvieran, los lotes entrarían a Bronze y `validate` los
    ignoraría en silencio: ni cargados, ni en cuarentena."""
    from src.pipeline.validate import FUENTES_NOTICIAS

    assert FUENTES_ARCHIVO
    assert FUENTES_ARCHIVO <= FUENTES_NOTICIAS


def test_cada_medio_declara_source_propio():
    """El archivo de El Financiero no puede mezclarse con su feed en vivo: son
    noticias de 2020 recuperadas en 2026, y el `source` es lo que impide
    confundirlas con lo que se ingirió en su momento."""
    sources = [m.source for m in MEDIOS.values()]
    assert len(sources) == len(set(sources))
    for clave, medio in MEDIOS.items():
        assert medio.source.endswith("_archivo"), clave


def test_el_normalizador_entiende_el_cuerpo_de_archivo():
    """La hemeroteca emite `content` y `link`; si el normalizador no los
    leyera, las noticias llegarían a Silver sin texto y sin URL."""
    from src.pipeline.validate import _fecha_de_entrada, _texto_de_entrada

    crudo = {"content": "Banorte reportó una utilidad de 15,288 millones.",
             "link": "https://ejemplo.mx/a.html", "published": "2020-03-23"}
    assert "Banorte" in _texto_de_entrada(crudo)
    assert _fecha_de_entrada(crudo) == "2020-03-23"


# --- Un mes incompleto no puede darse por hecho ------------------------------


def test_descubrir_reintenta_antes_de_rendirse(monkeypatch):
    """En el recorrido de 2018 se perdió `/empresas/` de octubre por un
    HTTPError suelto, y el mes quedó con 681 artículos en vez de ~1.100. Un
    fallo de red de un segundo no puede costar un mes de archivo."""
    import src.sources.hemeroteca as h

    llamadas = {"n": 0}

    class RespuestaOK:
        text = ""
        def raise_for_status(self): return None

    def falla_dos_veces(*a, **k):
        llamadas["n"] += 1
        if llamadas["n"] < 3:
            raise RuntimeError("503 transitorio")
        return RespuestaOK()

    monkeypatch.setattr(h.requests, "get", falla_dos_veces)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)

    h.descubrir("ejemplo.mx", "mercados", 2018, 10, patron_fecha=PATRON)
    assert llamadas["n"] == 3, "no reintentó lo suficiente"


def test_descubrir_propaga_el_fallo_si_no_hay_forma(monkeypatch):
    """Tras agotar los intentos la excepción SUBE. Quien llama decide, y lo
    que no puede hacer es dar el mes por bueno."""
    import src.sources.hemeroteca as h

    def siempre_falla(*a, **k):
        raise RuntimeError("503")

    monkeypatch.setattr(h.requests, "get", siempre_falla)
    monkeypatch.setattr(h.time, "sleep", lambda s: None)

    with pytest.raises(RuntimeError):
        h.descubrir("ejemplo.mx", "mercados", 2018, 10, patron_fecha=PATRON)


def test_un_mes_con_seccion_fallida_no_escribe_lote():
    """Escribirlo lo daría por hecho y el reanudado lo saltaría para siempre,
    dejándolo mutilado en silencio — que es exactamente lo que pasó con
    2018-10. Se comprueba sobre el código porque el comportamiento vive en el
    flujo de control, no en un valor de retorno."""
    import inspect

    from src.pipeline import backfill_noticias as b

    fuente = inspect.getsource(b.main)
    assert "incompleto" in fuente
    i_salto = fuente.index("SALTADO sin escribir")
    i_escribe = fuente.index("escribir_lote(")
    assert i_salto < i_escribe, "el abandono debe ocurrir ANTES de escribir el lote"
