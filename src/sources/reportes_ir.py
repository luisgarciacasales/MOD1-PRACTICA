"""Reporte narrativo trimestral por emisora (ampliación 15-ago-2026).

Se trata como una fuente de NOTICIAS más (`FUENTES_NOTICIAS` en validate.py):
mismo contrato `SilverNews`, mismo `enrich`. El ticker de cada emisora no se
inyecta a mano — el propio comunicado lo menciona en el encabezado
("BMV: GFNORTEO", "BMV: BOLSA A"...) y la extracción léxica ya existente
(`src/pipeline/extraccion.py`) lo detecta, igual que en cualquier otra
noticia. Eso significa que este adaptador no necesita tocar el contrato ni
`validate.py` más allá de registrar el nombre de la fuente.

Ninguna emisora expone RSS ni una URL de reporte 100% predecible por fórmula
(los nombres de archivo varían de un trimestre a otro), así que cada
localizador raspa la página listado en vivo. Tres funciones de localización,
no una genérica: Banorte y BOLSAA tienen sitio propio con su propia
estructura de URL; las otras 5 (Regional, BBAJIOO, GENTERA, GFINBURO, Q)
comparten el portal de divulgación de la BMV, pero incluso ahí el nombre del
PDF narrativo varía según la categoría regulatoria de la emisora — ver
`_localizar_bmv_informacionfinanciera` para el detalle. Ver también
`src/config/reportes_ir.py` para el porqué de cada URL y qué otras 8
emisoras del universo se investigaron y NO tienen narrativo en este portal.

Del PDF solo se extraen las primeras `PAGINAS_A_EXTRAER` páginas, no el
documento completo (41-85 páginas), y de esas se recorta hasta el marcador de
resumen ejecutivo (`_desde_el_resumen`): el resto son notas a los estados
financieros, que además ya cubre `yahoo_fundamentals` en forma estructurada;
traerlas aquí duplicaría dato sin aportar señal nueva.
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

# Generoso a propósito: el resumen ejecutivo no siempre está en la página 1.
# Verificado en las tres muestras piloto: BOLSAA arranca en la página 2,
# Banorte en la 3-4 y Regional en la 4 — antes hay portada, agenda de la
# llamada con analistas e índice. 12 páginas cubre el margen con holgura sin
# acercarse a las 41-85 páginas del documento completo.
PAGINAS_A_EXTRAER = 12

_CABECERAS = {
    "User-Agent": "Mozilla/5.0 (compatible; MOD1-PRACTICA/1.0; +academic research)"
}

# Frases que marcan el arranque real del resumen ejecutivo (o su equivalente
# en cada emisora). Se buscan combinadas porque ninguna es universal: Banorte
# y Regional usan "Resumen Ejecutivo", BOLSAA usa "Hitos Clave".
_MARCADORES_RESUMEN = ("resumen ejecutivo", "hitos clave del periodo")

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

_MES_CIERRE_TRIMESTRE = {1: 3, 2: 6, 3: 9, 4: 12}
_REZAGO_PUBLICACION_DIAS = 45


def fecha_aproximada_de_trimestre(anio: int, trimestre: int) -> date:
    """Fin de trimestre calendario + `_REZAGO_PUBLICACION_DIAS` — mismo
    rezago documentado en `_SQL_VALUATION` (transform.py) para el mismo
    motivo: un trimestre no se conoce el día que cierra.

    Determinista a partir del propio código de trimestre (`{n}T{aa}`), sin
    depender de qué fecha aparezca primero en el texto del PDF. Usada por
    `_localizar_banorte` (ver el porqué en su docstring: `_fecha_de_texto`
    encontraba la fecha de CORTE del periodo, no la de publicación) y por
    `src.pipeline.backfill_manual` para el histórico descargado a mano.
    """
    import calendar
    from datetime import timedelta

    mes = _MES_CIERRE_TRIMESTRE[trimestre]
    ultimo_dia = calendar.monthrange(anio, mes)[1]
    return date(anio, mes, ultimo_dia) + timedelta(days=_REZAGO_PUBLICACION_DIAS)


def ingerir() -> ResultadoFuente:
    """Descarga el reporte narrativo más reciente de cada emisora piloto.

    Fail-soft por emisora: que una no publique su PDF donde se esperaba (o el
    sitio cambie de estructura) no puede costar las demás.
    """
    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, emisora in enumerate(EMISORAS_IR):
        try:
            pdf_url, fecha_del_listado = _localizar_pdf(emisora)
            if pdf_url is None:
                fallidos.append(f"{emisora.ticker}: sin PDF localizable en el listado")
                continue

            texto_completo = _extraer_texto_pdf(pdf_url)
            if not texto_completo:
                fallidos.append(f"{emisora.ticker}: PDF sin texto extraíble")
                continue

            # Prioridad: la fecha del propio listado (cuando el sitio la da,
            # como el portal de la BMV) es más confiable que buscarla en el
            # texto. El "Comentarios de la Administración" de Regional no
            # trae un dateline claro cerca del resumen — solo fechas de notas
            # contables sueltas ("Al 30 de junio de 2025 y 2026...") que un
            # regex no puede distinguir de la fecha real de publicación.
            fecha = fecha_del_listado or _fecha_de_texto(texto_completo)
            if fecha is None:
                # Sin fecha confiable no se inventa una: mejor perder este
                # trimestre que contaminar published_at, que es parte de la
                # clave de idempotencia (calcular_guid).
                fallidos.append(f"{emisora.ticker}: no se encontró fecha en el texto")
                continue

            registros.append({
                "title": f"Reporte trimestral — {emisora.nombre}",
                "summary": _desde_el_resumen(texto_completo),
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


def _localizar_pdf(emisora: EmisoraIR) -> tuple[str | None, date | None]:
    """Devuelve `(url, fecha)`. `fecha` es None cuando el sitio no la da y
    hay que buscarla en el propio texto del PDF (`_fecha_de_texto`)."""
    resp = requests.get(emisora.listado_url, headers=_CABECERAS, timeout=TIMEOUT_LISTADO)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "lxml")

    if emisora.ticker == "GFNORTEO.MX":
        return _localizar_banorte(sopa, emisora.listado_url)
    if emisora.ticker == "BOLSAA.MX":
        return _localizar_bolsaa(sopa, emisora.listado_url), None
    # Regional, BBAJIOO, GENTERA, GFINBURO y Q vía el portal de la BMV — las
    # únicas 5 (de las 15 del universo) cuya categoría regulatoria publica
    # narrativo ahí. Ver docstring de `_localizar_bmv_informacionfinanciera`.
    return _localizar_bmv_informacionfinanciera(sopa, emisora.listado_url)


def _localizar_banorte(sopa: BeautifulSoup, base_url: str) -> tuple[str | None, date | None]:
    """El comunicado narrativo vive en `es/{año}/{n}T{aa}/`, con nombre de
    archivo que empieza EXACTAMENTE con el código de trimestre
    (`2T26.pdf`, `2T26_vc_.pdf`...); los anexos, riesgos y transcripciones
    casan con la misma carpeta pero se excluyen por nombre.

    BUG encontrado el 25-ago-2026 al desplegar el backfill histórico: antes
    devolvía `(url, None)` y dejaba la fecha a `_fecha_de_texto`, que en
    Banorte SIEMPRE encuentra "Información Financiera al {DD} de {MES} de
    {AAAA}" —la fecha de CORTE del periodo, no de publicación— porque
    aparece en la segunda línea del documento, antes que cualquier dateline
    real. Eso puso `published_at = fin_de_trimestre` en el registro ya en
    producción (2T26, ingerido 15-ago-2026) — lookahead bias: el mercado no
    conocía esos resultados el día que cerró el trimestre. Ahora la fecha
    sale del propio nombre de archivo (`fecha_aproximada_de_trimestre`,
    mismo rezago de 45 días que `_SQL_VALUATION`), determinista y sin
    depender de qué texto aparezca primero en el PDF."""
    candidatos = candidatos_banorte(sopa, base_url)
    if not candidatos:
        return None, None
    anio, qnum = max(candidatos)
    return candidatos[(anio, qnum)], fecha_aproximada_de_trimestre(anio, qnum)


def candidatos_banorte(sopa: BeautifulSoup, base_url: str) -> dict[tuple[int, int], str]:
    """TODOS los reportes narrativos que ofrece la página, por `(año, trimestre)`.

    La ingesta diaria solo quiere el último, pero el backfill de fundamentales
    quiere los que le falten a la serie, y ambos necesitan exactamente el mismo
    criterio para separar el comunicado de los anexos regulatorios. Vive aquí,
    en una sola función, para que no se dupliquen ni se desincronicen: el filtro
    lleva meses en producción y ya costó un bug de lookahead afinarlo.
    """
    encontrados: dict[tuple[int, int], str] = {}
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
        encontrados[(int(anio), int(qnum))] = urljoin(base_url, a["href"])
    return encontrados


def urls_trimestrales_banorte() -> dict[tuple[int, int], str]:
    """Consulta la página de RI y devuelve `{(año, trimestre): url}`.

    Solo Banorte: es la única emisora del universo cuyo sitio publica el
    listado histórico con una estructura de URL estable. Las que cuelgan del
    portal de la BMV exponen únicamente el trimestre vigente.
    """
    emisora = next(e for e in EMISORAS_IR if e.ticker == "GFNORTEO.MX")
    resp = requests.get(emisora.listado_url, headers=_CABECERAS, timeout=TIMEOUT_LISTADO)
    resp.raise_for_status()
    return candidatos_banorte(BeautifulSoup(resp.text, "lxml"), emisora.listado_url)


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


def _localizar_bmv_informacionfinanciera(
    sopa: BeautifulSoup, base_url: str
) -> tuple[str | None, date | None]:
    """Portal centralizado de divulgación de la BMV. El comunicado narrativo
    es el PDF cuyo nombre empieza con `sominfin_`; el que empieza con
    `infinsom_` en la misma carpeta son los estados financieros tabulados
    (ya cubiertos por `yahoo_fundamentals`). El ID numérico crece con cada
    presentación, así que el mayor es el más reciente.

    El nombre trae además el periodo de presentación (`{cat}infin_ID_AAAA-MM_N`)
    — más confiable que buscar una fecha dentro del PDF: el "Comentarios de
    la Administración" de Regional no trae un dateline claro, solo fechas de
    notas contables sueltas que un regex no puede distinguir de la real. Se
    usa el día 1 del mes como aproximación explícita, no exacta.

    IMPORTANTE (descubierto al mapear las 12 emisoras restantes, 15-ago-2026):
    el prefijo `{cat}` NO es fijo — depende de la clasificación regulatoria de
    la emisora ante la BMV, no del ticker. Verificado con curl crudo contra
    las 4 categorías que sí tienen narrativo:

        SOFOM               som   infinsom/sominfin_...   (Regional)
        Banco               bnc   infinbnc/bncinfin_...   (BanBajío, Gentera)
        Grupo financiero     gps   infingps/gpsinfin_...   (Inbursa)
        Aseguradora         asg   infinasg/asginfin_...   (Quálitas)

    Los corporativos no financieros (Walmex, América Móvil, Grupo México,
    CEMEX, FEMSA, Alsea) NO publican este tipo de documento en este portal —
    solo XBRL y, en algunos casos, un PDF tabulado sin narrativo separado.
    BBVA.MX y SANN.MX (matrices españolas vía SIC) tampoco: su última
    divulgación periódica es un 10-K/8-K anual, no un narrativo trimestral
    mexicano — consistente con la salvedad ya documentada en
    `src/config/tickers.py` sobre su exposición diluida a México. Para esas
    8 emisoras, este localizador no encuentra nada — no es un fallo, es que
    el documento no existe aquí.
    """
    mejor: tuple[int, str, date | None] | None = None
    for a in sopa.find_all("a", href=True):
        m = re.search(
            r"/docs-pub/infin(bnc|gps|asg|som)/\1infin_(\d+)_(\d{4})-(\d{2})_[^/\"]+\.pdf$",
            a["href"],
        )
        if not m:
            continue
        _cat, id_num, anio, mes = m.groups()
        id_num = int(id_num)
        try:
            fecha = date(int(anio), int(mes), 1)
        except ValueError:
            fecha = None
        if mejor is None or id_num > mejor[0]:
            mejor = (id_num, urljoin(base_url, a["href"]), fecha)
    if mejor is None:
        return None, None
    return mejor[1], mejor[2]


# --- Extracción del PDF -------------------------------------------------


def _extraer_texto_pdf(pdf_url: str) -> str:
    from pypdf import PdfReader

    resp = requests.get(pdf_url, headers=_CABECERAS, timeout=TIMEOUT_PDF)
    resp.raise_for_status()
    lector = PdfReader(io.BytesIO(resp.content))
    paginas = lector.pages[:PAGINAS_A_EXTRAER]
    return "\n".join(p.extract_text() or "" for p in paginas).strip()


# `pypdf` a veces separa palabras contiguas con espacios dobles o triples por
# artefactos de kerning del PDF ("RESUMEN  EJECUTIVO", visto en Regional pero
# no en Banorte ni BOLSAA — depende del generador de cada emisora). `\s+`
# entre palabras tolera eso; una subcadena exacta no lo habría encontrado
# nunca y habría devuelto el documento completo sin recortar.
_PATRONES_RESUMEN = tuple(
    re.compile(r"\s+".join(re.escape(palabra) for palabra in marcador.split()), re.IGNORECASE)
    for marcador in _MARCADORES_RESUMEN
)


def _desde_el_resumen(texto: str) -> str:
    """Recorta la portada y el índice, que no aportan señal para NER ni
    sentimiento.

    La PRIMERA aparición del marcador suele ser la entrada del índice (le
    sigue un número de página, no prosa); la SEGUNDA es donde arranca la
    sección de verdad. Con una sola aparición se usa esa — más vale texto con
    algo de portada que perder el reporte entero por un índice ausente.
    """
    posiciones = sorted(
        m.start() for patron in _PATRONES_RESUMEN for m in patron.finditer(texto)
    )
    if not posiciones:
        return texto
    ancla = posiciones[1] if len(posiciones) > 1 else posiciones[0]
    return texto[ancla:]


def _fecha_de_texto(texto: str) -> date | None:
    """Primera fecha en español que aparece en el texto ('21 de julio de
    2026'). Puede ser la fecha de publicación (dateline) o el cierre del
    periodo que reporta, según cómo redacte cada emisora — es una
    aproximación aceptada, documentada aquí en vez de asumida en silencio.

    `pypdf` a veces separa dígitos contiguos con un espacio por artefactos de
    kerning del PDF ('2 1' en vez de '21'; visto en el propio BOLSAA) — se
    compacta antes de buscar, solo para esta búsqueda, sin tocar el texto que
    se guarda.
    """
    compactado = re.sub(r"(?<=\d)\s(?=\d)", "", texto)
    m = _PATRON_FECHA_ES.search(compactado)
    if not m:
        return None
    dia, mes_texto, anio = m.groups()
    mes = _MESES_ES.get(mes_texto.lower())
    try:
        return date(int(anio), mes, int(dia))
    except ValueError:
        return None
