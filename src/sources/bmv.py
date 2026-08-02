"""Eventos Relevantes de la BMV (PRD §3.1) — scraping ligero.

ESTADO CONOCIDO (verificado el 2026-08-01 desde mi-pc): **esta fuente falla**.

La página pública es una SPA: el HTML servido no contiene el listado, que pinta
JavaScript tras arrancar. Los endpoints internos probados
(`/api/BMV/eventosRelevantes`, `/api/sitios/…`, variantes de `parametroSolicitud`)
devuelven 404 en el gateway WSO2 de la BMV.

Es exactamente el riesgo nº3 del PRD §9 — *"Scraping BMV frágil"*, probabilidad
**Alta** — cuya mitigación documentada es el fail-soft por fuente. El adaptador
se deja implementado contra la estructura de tabla esperada: si la BMV vuelve a
servir el listado en HTML, funciona sin cambios; si no, informa con precisión
en vez de fingir un lote vacío.

Salidas posibles para desbloquearlo (ninguna es Fase 1):
  · Capturar el XHR real con las devtools del navegador y apuntar aquí.
  · Navegador headless (Playwright) — el PRD §2.2 excluye scraping pesado.
  · EMISNET/InfoFinanciera bajo acuerdo de uso con la BMV.
"""

from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.config.sources import FUENTES_POR_ID
from src.sources.base import ResultadoFuente
from src.sources.http import sesion_simple

TIMEOUT = 30


def ingerir(source_id: str = "bmv_eventos") -> ResultadoFuente:
    fuente = FUENTES_POR_ID[source_id]
    try:
        with sesion_simple() as sesion:
            respuesta = sesion.get(fuente.url, timeout=TIMEOUT)
            respuesta.raise_for_status()

        registros = _extraer_tabla(respuesta.text, source_id)
        if not registros:
            raise ValueError(
                "el HTML no contiene tabla de eventos — la página se renderiza "
                "por JavaScript y los endpoints internos devuelven 404 "
                "(riesgo nº3 del PRD §9)"
            )
        return ResultadoFuente(source=source_id, categoria="news", registros=registros)
    except Exception as exc:  # noqa: BLE001 — fail-soft es el contrato
        return ResultadoFuente.fallo(source_id, "news", exc)


def _extraer_tabla(html: str, source_id: str) -> list[dict[str, Any]]:
    """Extrae filas de la primera tabla con cabeceras reconocibles.

    Mapea por nombre de cabecera y no por posición: si la BMV reordena las
    columnas, un índice fijo produciría datos cruzados en silencio, que es
    peor que no extraer nada.
    """
    sopa = BeautifulSoup(html, "lxml")
    registros: list[dict[str, Any]] = []

    for tabla in sopa.find_all("table"):
        filas = tabla.find_all("tr")
        if len(filas) < 2:
            continue

        cabeceras = [c.get_text(strip=True).lower() for c in filas[0].find_all(["th", "td"])]
        if not any("evento" in c or "emisora" in c or "fecha" in c for c in cabeceras):
            continue

        for fila in filas[1:]:
            celdas = [c.get_text(strip=True) for c in fila.find_all("td")]
            if not celdas or not any(celdas):
                continue
            registro: dict[str, Any] = dict(zip(cabeceras, celdas, strict=False))
            enlace = fila.find("a", href=True)
            if enlace:
                registro["href"] = enlace["href"]
            registro["source"] = source_id
            registros.append(registro)

        if registros:
            break

    return registros
