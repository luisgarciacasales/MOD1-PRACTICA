"""Recuperación de noticias históricas desde el archivo del Internet Archive.

Dos pasos separados a propósito, porque tienen coste muy distinto:

    descubrir()  → pregunta al índice CDX qué URLs existen. Una consulta por
                   sección y mes devuelve cientos de artículos. Barato.
    recuperar()  → baja UN artículo. Es lo caro, y por eso solo se piden los
                   que el filtro por titular ya seleccionó.

**Ritmo.** El Internet Archive ofrece esto gratis y sin autenticación. Se pide
una vez por segundo y sin paralelizar. Un recorrido de cinco años tarda horas
en lugar de minutos, y esa es exactamente la intención: la alternativa es
convertir un servicio público en un problema para quien lo mantiene.

**Sufijo `id_`.** `web.archive.org/web/{ts}id_/{url}` devuelve el HTML tal como
se archivó, sin la barra de navegación del Archive ni sus reescrituras de
enlaces. Sin él habría que limpiar su interfaz de cada página, y esa limpieza
se rompería cada vez que el Archive cambiara su plantilla.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import requests
from bs4 import BeautifulSoup

CDX = "http://web.archive.org/cdx/search/cdx"
SNAPSHOT = "https://web.archive.org/web/{ts}id_/{url}"

PAUSA = 1.1          # segundos entre peticiones al Archive
TIMEOUT_CDX = 120
TIMEOUT_ART = 60
MIN_PARRAFO = 40     # un <p> más corto que esto suele ser pie de foto o menú
MIN_CUERPO = 250     # por debajo, el artículo no aporta nada al NER

_CABECERAS = {
    "User-Agent": "MOD1-PRACTICA/1.0 (analitica academica de equity BMV; contacto via github)"
}


@dataclass(frozen=True)
class Hallazgo:
    """Una URL archivada que el índice devolvió, antes de descargar nada."""

    url: str
    timestamp: str
    titular: str          # derivado de la propia URL
    fecha: str            # YYYY-MM-DD, del identificador del artículo


def _titular_de_url(url: str, patron_fecha: str) -> str:
    """El slug de la URL, legible. Sirve para filtrar sin descargar."""
    slug = url.rstrip("/").split("/")[-1]
    slug = re.sub(patron_fecha, "", slug)
    slug = re.sub(r"\.html?$", "", slug)
    return re.sub(r"[-_]+", " ", slug).strip()


def descubrir(dominio: str, seccion: str, anio: int, mes: int, *,
              patron_fecha: str, limite: int = 5000) -> list[Hallazgo]:
    """Qué artículos de esa sección archivó el Archive ese mes.

    `collapse=urlkey` deduplica: el mismo artículo suele estar archivado varias
    veces y solo interesa una copia. `statuscode:200` descarta los snapshots que
    guardaron un error del servidor en lugar del artículo.
    """
    desde, hasta = f"{anio}{mes:02d}01", f"{anio}{mes:02d}31"
    params = {
        "url": f"{dominio}/{seccion}/*",
        "from": desde, "to": hasta,
        "output": "text", "fl": "timestamp,original",
        "collapse": "urlkey", "filter": "statuscode:200",
        "limit": str(limite),
    }
    resp = requests.get(CDX, params=params, headers=_CABECERAS, timeout=TIMEOUT_CDX)
    resp.raise_for_status()

    hallazgos: list[Hallazgo] = []
    for linea in resp.text.splitlines():
        partes = linea.split(" ", 1)
        if len(partes) != 2:
            continue
        ts, url = partes
        # Normaliza el doble slash que aparece en algunas capturas y descarta
        # las URLs con basura de tracking pegada al final.
        url = re.sub(r"(?<!:)//+", "/", url)
        if "?" in url or len(url) > 400:
            continue
        m = re.search(patron_fecha, url)
        if not m:
            continue
        crudo = m.group(1)
        try:
            fecha = datetime.strptime(crudo, "%Y%m%d").date().isoformat()
        except ValueError:
            continue
        hallazgos.append(
            Hallazgo(url=url, timestamp=ts,
                     titular=_titular_de_url(url, patron_fecha), fecha=fecha)
        )
    return hallazgos


def menciona_emisora(titular: str, alias: dict[str, tuple[str, ...]]) -> list[str]:
    """Tickers cuyo nombre aparece en el titular. El filtro que evita descargas.

    Se compara sobre el titular en minúsculas y sin separadores, porque la URL
    trae el título con guiones. No pretende ser el NER: es un cedazo grueso
    cuyo único trabajo es decidir qué vale la pena pedirle al Archive. El
    veredicto real lo dará `enrich` sobre el texto completo.
    """
    texto = f" {titular.lower()} "
    encontrados = []
    for ticker, nombres in alias.items():
        for nombre in nombres:
            if len(nombre) >= 4 and f" {nombre.lower()} " in texto:
                encontrados.append(ticker)
                break
    return encontrados


def _cuerpo(html: str) -> tuple[str, str]:
    """Devuelve `(titulo, texto)` del artículo archivado.

    El cuerpo se elige por MASA DE TEXTO, no por un selector de clase: cada
    medio nombra sus contenedores a su manera y los renombra al rediseñar. Se
    toma el `<article>` (o `<div>`) cuyos párrafos suman más caracteres, que en
    una página de noticia es siempre el artículo y no el menú ni los
    relacionados.
    """
    sopa = BeautifulSoup(html, "lxml")
    for basura in sopa.find_all(["script", "style", "nav", "aside", "footer", "form"]):
        basura.decompose()

    titulo = ""
    art = sopa.find("article", attrs={"data-title": True})
    if art:
        titulo = art["data-title"].strip()
    if not titulo and sopa.title:
        titulo = sopa.title.get_text().split("|")[0].strip()

    mejor, mejor_largo = None, 0
    for cand in sopa.find_all(["article", "div", "section"]):
        largo = sum(len(p.get_text(strip=True)) for p in cand.find_all("p", recursive=False))
        if largo > mejor_largo:
            mejor, mejor_largo = cand, largo

    if mejor is None:
        return titulo, ""
    parrafos = [
        p.get_text(" ", strip=True)
        for p in mejor.find_all("p", recursive=False)
        if len(p.get_text(strip=True)) >= MIN_PARRAFO
    ]
    return titulo, "\n\n".join(parrafos)


def recuperar(hallazgo: Hallazgo) -> dict | None:
    """Baja un artículo del archivo. `None` si no se pudo o vino vacío.

    Fail-soft por artículo: en un recorrido de miles, algunos snapshots están
    truncados o guardaron una página de error. Que uno falle no puede costar
    los demás, y tampoco merece un reintento — hay otros cientos esperando.
    """
    time.sleep(PAUSA)
    try:
        resp = requests.get(
            SNAPSHOT.format(ts=hallazgo.timestamp, url=hallazgo.url),
            headers=_CABECERAS, timeout=TIMEOUT_ART,
        )
        resp.raise_for_status()
        resp.encoding = resp.encoding or "utf-8"
        titulo, texto = _cuerpo(resp.text)
    except Exception:  # noqa: BLE001
        return None

    if len(texto) < MIN_CUERPO:
        return None

    # `link` y no `url`: es el nombre que ya interpreta `normalizar_noticia`
    # para todas las fuentes de noticias, y no hay motivo para que el archivo
    # obligue a ensanchar el normalizador con un sinónimo más.
    return {
        "link": hallazgo.url,
        "title": titulo or hallazgo.titular,
        "content": texto,
        "published": hallazgo.fecha,
        "archivado_en": hallazgo.timestamp,
        "recuperado_at": datetime.now(UTC).isoformat(),
    }
