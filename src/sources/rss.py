"""Feeds RSS de medios financieros mexicanos (PRD §3.2).

Estado verificado el 2026-08-01 desde el contenedor en mi-pc:
  · El Financiero    → 100 entradas. El feed por categoría solo daba 2; se usa
                       el general y el filtrado se hace aguas abajo.
  · Bloomberg Línea  → 100 entradas, pero **solo** con `?outputType=xml`.
  · El Economista    → HTTP 403 incluso con cabeceras de navegador. El WAF
                       bloquea IPs de datacenter. Falla en soft, por diseño.
"""

from __future__ import annotations

import time
from typing import Any

import feedparser

from src.config.sources import FUENTES_POR_ID
from src.sources.base import ResultadoFuente
from src.sources.http import sesion_simple

TIMEOUT = 25


def ingerir(source_id: str) -> ResultadoFuente:
    """Descarga un feed y devuelve sus entradas sin transformar."""
    fuente = FUENTES_POR_ID[source_id]
    try:
        with sesion_simple() as sesion:
            respuesta = sesion.get(fuente.url, timeout=TIMEOUT)
            respuesta.raise_for_status()

        # feedparser sobre los bytes crudos, no sobre la URL: así la descarga
        # pasa por nuestras cabeceras y un error HTTP es explícito en vez de
        # convertirse en un feed vacío silencioso.
        feed = feedparser.parse(respuesta.content)
        if feed.bozo and not feed.entries:
            raise ValueError(f"feed no parseable: {feed.bozo_exception}")

        registros = [_entrada_a_dict(e, source_id) for e in feed.entries]
        if not registros:
            raise ValueError("el feed no devolvió entradas")

        return ResultadoFuente(source=source_id, categoria="news", registros=registros)
    except Exception as exc:  # noqa: BLE001 — fail-soft es el contrato
        return ResultadoFuente.fallo(source_id, "news", exc)


def _entrada_a_dict(entrada: Any, source_id: str) -> dict[str, Any]:
    """Convierte la entrada de feedparser a dict serializable.

    Es una conversión de formato (XML→JSON), que el PRD §5.1 contempla
    explícitamente, no una limpieza: no se recorta, normaliza ni descarta nada.
    Se añade `source` para poder reconstruir el origen desde Bronze.
    """
    crudo = {k: v for k, v in dict(entrada).items() if not k.startswith("_")}
    crudo["source"] = source_id
    return _serializable(crudo)


def _serializable(valor: Any) -> Any:
    """feedparser devuelve `time.struct_time` y objetos propios que json no sabe
    escribir. Se convierten a tipos básicos preservando el contenido."""
    if isinstance(valor, time.struct_time):
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", valor)
    if isinstance(valor, dict):
        return {str(k): _serializable(v) for k, v in valor.items()}
    if isinstance(valor, list | tuple):
        return [_serializable(v) for v in valor]
    if isinstance(valor, str | int | float | bool | type(None)):
        return valor
    return str(valor)
