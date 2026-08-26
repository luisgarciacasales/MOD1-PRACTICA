"""Eventos relevantes por emisora vía el portal de divulgación de la BMV
(reemplazo de `bmv_eventos`, 25-ago-2026 — ver `src/config/eventos_relevantes.py`
para el porqué completo).

Mismo trato que una fuente de noticias más (`FUENTES_NOTICIAS` en
validate.py), igual que `reportes_ir`: el asunto del evento ES el contenido,
no se descarga el documento adjunto — la mayoría son XBRL/PDF de trámite
regulatorio (avisos, calificaciones) sin texto adicional que aporte señal
sobre el resumen ya visible en la tabla.

Filtro de categoría: la página de cada emisora trae VARIAS tablas de eventos
con distinta señal — "Aviso al Público Inversionista" (trámite XBRL
recurrente, sin valor NLP), "INICIA SUBASTA DE VOLATILIDAD" (microestructura
de mercado, no evento corporativo) y la que sí importa: documentos bajo
`/docs-pub/eventoca/`, que es donde viven M&A, calificaciones crediticias,
recompras de acciones. Se filtra por esa ruta en el href, no por posición de
tabla — más robusto si la BMV reordena las secciones de la página.
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from src.config.eventos_relevantes import EMISORAS_EVENTOS, EmisoraEventos
from src.sources.base import ResultadoFuente

_BASE_URL = "https://www.bmv.com.mx"
_TIMEOUT = 20
_PAUSA_ENTRE_EMISORAS = 1.0

_CABECERAS = {
    "User-Agent": "Mozilla/5.0 (compatible; MOD1-PRACTICA/1.0; +academic research)"
}

# Solo esta ruta es "evento corporativo relevante" — ver docstring del módulo.
_RUTA_EVENTO_MATERIAL = re.compile(r"/docs-pub/eventoca/[^\"']+")

_PATRON_FECHA = re.compile(r"(\d{2})-(\d{2})-(\d{4})\s+(\d{2}):(\d{2})")


def ingerir() -> ResultadoFuente:
    """Descarga los eventos relevantes recientes de cada emisora mapeada.

    Fail-soft por emisora: que una no tenga eventos en la ventana visible de
    la página (o el sitio cambie de estructura) no puede costar las demás.
    Un conteo de 0 no es necesariamente un fallo — no todas las emisoras
    tienen M&A o acción de calificadora en cualquier ventana dada.
    """
    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, emisora in enumerate(EMISORAS_EVENTOS):
        try:
            eventos = _eventos_de(emisora)
            if not eventos:
                fallidos.append(f"{emisora.ticker}: sin eventos materiales en la ventana visible")
                continue
            for fecha, asunto, url_doc in eventos:
                registros.append({
                    # El nombre va SIEMPRE en el contenido, no solo en el
                    # asunto: el asunto no siempre menciona a la emisora por
                    # nombre ("Adquisición de Entidad Financiera" no dice
                    # "Inbursa"), y a diferencia de un RSS genérico aquí SÍ
                    # sabemos con certeza el ticker — desperdiciarla y dejar
                    # que la extracción léxica adivine sería peor que esto.
                    "title": asunto,
                    "summary": f"{emisora.nombre} ({emisora.ticker}) — evento relevante: {asunto}",
                    "link": url_doc,
                    "published": fecha.isoformat(),
                    "source": "eventos_relevantes",
                })
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{emisora.ticker}: {type(exc).__name__}")

        if i < len(EMISORAS_EVENTOS) - 1:
            time.sleep(_PAUSA_ENTRE_EMISORAS)

    if not registros:
        return ResultadoFuente(
            source="eventos_relevantes", categoria="news",
            error=f"ninguna emisora devolvió eventos ({'; '.join(fallidos)})"[:500],
        )

    resultado = ResultadoFuente(source="eventos_relevantes", categoria="news", registros=registros)
    if fallidos:
        resultado.registros.append(
            {"_parciales": True, "tickers_fallidos": fallidos, "source": "eventos_relevantes"}
        )
    return resultado


def _eventos_de(emisora: EmisoraEventos) -> list[tuple[datetime, str, str]]:
    # La "clave" es decorativa (verificado en vivo, mismo comportamiento que
    # el portal de reportes_ir): cualquier texto antes del ID funciona, así
    # que se usa el ticker completo sin adivinar la serie accionaria.
    clave = emisora.ticker.removesuffix(".MX")
    url = f"{_BASE_URL}/es/emisoras/eventosrelevantes/{clave}-{emisora.id_bmv}-CGEN_CAPIT"
    resp = requests.get(url, headers=_CABECERAS, timeout=_TIMEOUT)
    resp.raise_for_status()
    sopa = BeautifulSoup(resp.text, "lxml")

    eventos: list[tuple[datetime, str, str]] = []
    for fila in sopa.find_all("tr"):
        enlace = fila.find("a", href=_RUTA_EVENTO_MATERIAL)
        if enlace is None:
            continue
        celdas = fila.find_all("td")
        if len(celdas) < 2:
            continue
        fecha = _parsear_fecha(celdas[0].get_text(strip=True))
        asunto = celdas[1].get_text(strip=True)
        if fecha is None or not asunto:
            continue
        eventos.append((fecha, asunto, urljoin(_BASE_URL, enlace["href"])))

    return eventos


def _parsear_fecha(texto: str) -> datetime | None:
    """'27-02-2025 18:25' → datetime. Formato fijo del portal (verificado
    contra varias emisoras) — a diferencia del narrativo de reportes_ir, aquí
    sí hay hora exacta, así que no hace falta la aproximación al día 1."""
    m = _PATRON_FECHA.search(texto)
    if not m:
        return None
    dia, mes, anio, hora, minuto = (int(x) for x in m.groups())
    try:
        return datetime(anio, mes, dia, hora, minuto)
    except ValueError:
        return None
