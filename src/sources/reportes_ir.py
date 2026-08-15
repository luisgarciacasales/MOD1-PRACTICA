"""Reporte narrativo trimestral por emisora (ampliación 15-ago-2026).

Se trata como una fuente de NOTICIAS más (`FUENTES_NOTICIAS` en validate.py):
mismo contrato `SilverNews`, mismo `enrich`. El ticker de cada emisora no se
inyecta a mano — el propio comunicado lo menciona en el encabezado
("BMV: GFNORTEO", "BMV: BOLSA A"...) y la extracción léxica ya existente
(`src/pipeline/extraccion.py`) lo detecta, igual que en cualquier otra
noticia. Eso significa que este adaptador no necesita tocar el contrato ni
`validate.py` más allá de registrar el nombre de la fuente.

Ninguna de las tres emisoras piloto expone RSS ni una URL de reporte 100%
predecible por fórmula (los nombres de archivo varían de un trimestre a
otro), así que cada localizador raspa la página listado en vivo. Son tres
funciones distintas, no una genérica, porque las tres páginas tienen
estructuras de URL distintas — ver `src/config/reportes_ir.py` para el porqué
de cada una.

Del PDF solo se extraen las primeras `PAGINAS_A_EXTRAER` páginas, no el
documento completo (41-85 páginas): en las tres muestras verificadas el
resumen ejecutivo / "Discusión y Análisis de la Administración" —el texto que
de verdad interesa para NER y sentimiento— arranca en la página 1. El resto
son notas a los estados financieros, que además ya cubre `yahoo_fundamentals`
en forma estructurada; traerlas aquí duplicaría dato sin aportar señal nueva.
"""

from __future__ import annotations

import io
import re
import time
from datetime import date
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config.reportes_ir import EMISORAS_IR, EmisoraIR
from src.sources.base import ResultadoFuente

TIMEOUT_LISTADO = 20
TIMEOUT_PDF = 40
PAUSA_ENTRE_EMISORAS = 1.0
PAGINAS_A_EXTRAER = 3

_CABECERAS = {
    "User-Agent": "Mozilla/5.0 (compatible; MOD1-PRACTICA/1.0; +academic research)"
}

# Filenames que casan con el patrón de trimestre pero NO son el comunicado
# narrativo: anexos regulatorios, reporte de riesgos, certificaciones,
# transcripciones y material de la llamada con analistas.
_EXCLUIR_BANORTE = re.compile(r"anexo|riesgo|certificac|transcript|conference|guidance", re.IGNORECASE)

_MESES_ES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}
_PATRON_FECHA_ES = re.compile(
    r"(\d{1,2})\s+de\s+(" + "|".join(_MESES_ES) + r")\s+de\s+(\d{4})", re.IGNORECASE
)


def ingerir() -> ResultadoFuente:
    """Descarga el reporte narrativo más reciente de cada emisora piloto.

    Fail-soft por emisora: que una no publique su PDF donde se esperaba (o el
    sitio cambie de estructura) no puede costar las demás.
    """
    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, emisora in enumerate(EMISORAS_IR):
        try:
            pdf_url = _localizar_pdf(emisora)
            if pdf_url is None:
                fallidos.append(f"{emisora.ticker}: sin PDF localizable en el listado")
                continue

            texto = _extraer_narrativo(pdf_url)
            if not texto:
                fallidos.append(f"{emisora.ticker}: PDF sin texto extraíble")
                continue

            fecha = _fecha_de_texto(texto)
            if fecha is None:
                # Sin fecha confiable no se inventa una: mejor perder este
                # trimestre que contaminar published_at, que es parte de la
                # clave de idempotencia (calcular_guid).
                fallidos.append(f"{emisora.ticker}: no se encontró fecha en el texto")
                continue

            registros.append({
                "title": f"Reporte trimestral — {emisora.nombre}",
                "summary": texto,
                "link": pdf_url,
                "published": fecha.isoformat(),
                "source": "reportes_ir",
            })
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{emisora.ticker}: {type(exc).__name__}")

        if i < len(EMISORAS_IR) - 1:
            time.sleep(PAUSA_ENTRE_EMISORAS)

    if not registros:
        return ResultadoFuente(
            source="reportes_ir", categoria="news",
            error=f"ninguna emisora devolvió reporte ({'; '.join(fallidos)})"[:500],
        )

    resultado = ResultadoFuente(source="reportes_ir", categoria="news", registros=registros)
    if fallidos:
        resultado.registros.append(
            {"_parciales": True, "tickers_fallidos": fallidos, "source": "reportes_ir"}
        )
    return resultado


# --- Localizadores del PDF, uno por sitio -----------------------------------


def _localizar_pdf(emisora: EmisoraIR) -> str | None:
    resp = requests.get(emisora.listado_url, headers=_CABECERAS, timeout=TIMEOUT_LISTADO)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "lxml")

    if emisora.ticker == "GFNORTEO.MX":
        return _localizar_banorte(sopa, emisora.listado_url)
    if emisora.ticker == "BOLSAA.MX":
        return _localizar_bolsaa(sopa, emisora.listado_url)
    # Regional y cualquier otra emisora futura vía el portal de la BMV.
    return _localizar_bmv_informacionfinanciera(sopa, emisora.listado_url)


def _localizar_banorte(sopa: BeautifulSoup, base_url: str) -> str | None:
    """El comunicado narrativo vive en `es/{año}/{n}T{aa}/`, con nombre de
    archivo que empieza EXACTAMENTE con el código de trimestre
    (`2T26.pdf`, `2T26_vc_.pdf`...); los anexos, riesgos y transcripciones
    casan con la misma carpeta pero se excluyen por nombre."""
    mejor: tuple[tuple[int, int], str] | None = None
    for a in sopa.find_all("a", href=True):
        m = re.search(
            r"quarterly-results/es/(\d{4})/(\d)T(\d{2})/([^/\"]+\.pdf)$", a["href"]
        )
        if not m:
            continue
        anio, qnum, yy, archivo = m.groups()
        if _EXCLUIR_BANORTE.search(archivo):
            continue
        if not re.match(rf"^{qnum}T{yy}(_.*)?\.pdf$", archivo, re.IGNORECASE):
            continue
        clave = (int(anio), int(qnum))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, urljoin(base_url, a["href"]))
    return mejor[1] if mejor else None


def _localizar_bolsaa(sopa: BeautifulSoup, base_url: str) -> str | None:
    """`PRESS RELEASE {n}T{aa}.pdf` bajo `/docs-pub/reporteTrimestral/` — el
    espacio literal del nombre de archivo se codifica al construir la URL."""
    mejor: tuple[tuple[int, int], str] | None = None
    for a in sopa.find_all("a", href=True):
        m = re.search(r"/docs-pub/reporteTrimestral/PRESS RELEASE (\d)T(\d{2})\.pdf$", a["href"])
        if not m:
            continue
        qnum, yy = m.groups()
        clave = (int(yy), int(qnum))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, urljoin(base_url, a["href"]))
    if mejor is None:
        return None
    return mejor[1].replace(" ", "%20")


def _localizar_bmv_informacionfinanciera(sopa: BeautifulSoup, base_url: str) -> str | None:
    """Portal centralizado de divulgación de la BMV. El comunicado narrativo
    es el PDF cuyo nombre empieza con `sominfin_`; el que empieza con
    `infinsom_` en la misma carpeta son los estados financieros tabulados
    (ya cubiertos por `yahoo_fundamentals`). El ID numérico crece con cada
    presentación, así que el mayor es el más reciente."""
    mejor: tuple[int, str] | None = None
    for a in sopa.find_all("a", href=True):
        m = re.search(r"/docs-pub/infinsom/sominfin_(\d+)_[^/\"]+\.pdf$", a["href"])
        if not m:
            continue
        id_num = int(m.group(1))
        if mejor is None or id_num > mejor[0]:
            mejor = (id_num, urljoin(base_url, a["href"]))
    return mejor[1] if mejor else None


# --- Extracción del PDF -------------------------------------------------


def _extraer_narrativo(pdf_url: str) -> str:
    from pypdf import PdfReader

    resp = requests.get(pdf_url, headers=_CABECERAS, timeout=TIMEOUT_PDF)
    resp.raise_for_status()
    lector = PdfReader(io.BytesIO(resp.content))
    paginas = lector.pages[:PAGINAS_A_EXTRAER]
    return "\n".join(p.extract_text() or "" for p in paginas).strip()


def _fecha_de_texto(texto: str) -> date | None:
    """Primera fecha en español que aparece en el texto ('21 de julio de
    2026'). Puede ser la fecha de publicación (dateline) o el cierre del
    periodo que reporta, según cómo redacte cada emisora — es una
    aproximación aceptada, documentada aquí en vez de asumida en silencio."""
    m = _PATRON_FECHA_ES.search(texto)
    if not m:
        return None
    dia, mes_texto, anio = m.groups()
    mes = _MESES_ES.get(mes_texto.lower())
    try:
        return date(int(anio), mes, int(dia))
    except ValueError:
        return None
