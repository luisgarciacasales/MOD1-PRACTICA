"""Google News RSS con consultas dirigidas (PRD §3.2, ampliación 2026-08-04).

Las consultas viven en `src/config/google_news.py`; aquí solo se descargan y se
convierten a registros de Bronze **sin transformar**.

Todas las consultas se agrupan en **un solo `source`** (`google_news`) en vez de
una fuente por consulta. Con 14 consultas, lo contrario habría inflado el enum
del contrato y el `CHECK` de la tabla con 14 valores que además cambiarían cada
vez que se ajusta el catálogo. La trazabilidad no se pierde: cada registro lleva
`_consulta` con la etiqueta que lo produjo.

Dos particularidades del formato de Google que se manejan aquí:

1. El campo `source` de cada entrada es el **medio que publicó** el artículo
   (`{'title': 'El Economista', ...}`), y colisiona con el `source` que el
   pipeline usa para identificar la fuente de ingesta. Se preserva como
   `medio_original` antes de que se sobrescriba.
2. El `link` apunta a `news.google.com/rss/articles/...` y redirige al medio.
   Se conserva tal cual: es estable por artículo, y como el `guid` se calcula a
   partir de la URL, resolverlo cambiaría la clave natural de todo el histórico.
"""

from __future__ import annotations

import time
from typing import Any

import feedparser

from src.config.google_news import CONSULTAS, Consulta, url_de
from src.sources.base import ResultadoFuente
from src.sources.http import sesion_simple

TIMEOUT = 25

# Pausa entre consultas. Google News no documenta límites, pero 14 peticiones
# seguidas desde la misma IP es exactamente el patrón que dispara throttling.
PAUSA = 0.8

SOURCE_ID = "google_news"


def ingerir(
    *,
    consultas: tuple[Consulta, ...] = CONSULTAS,
    ventana_dias: int | None = None,
) -> ResultadoFuente:
    """Ejecuta todas las consultas y devuelve sus entradas sin transformar.

    Fail-soft **por consulta además de por fuente**: que Google rechace una no
    puede costar las otras trece. Solo se declara caída la fuente si ninguna
    devolvió resultados.
    """
    from src.config.google_news import VENTANA_DIAS

    ventana = ventana_dias or VENTANA_DIAS
    registros: list[dict[str, Any]] = []
    fallidas: list[str] = []
    vistos: set[str] = set()

    try:
        sesion = sesion_simple()
    except Exception as exc:  # noqa: BLE001
        return ResultadoFuente.fallo(SOURCE_ID, "news", exc)

    with sesion:
        for i, consulta in enumerate(consultas):
            try:
                respuesta = sesion.get(url_de(consulta, ventana_dias=ventana), timeout=TIMEOUT)
                respuesta.raise_for_status()
                feed = feedparser.parse(respuesta.content)

                nuevos = 0
                for entrada in feed.entries:
                    crudo = _entrada_a_dict(entrada, consulta)
                    # Deduplicación **dentro del lote**: las consultas se solapan
                    # (una nota sobre Banorte y fintechs aparece en dos) y no
                    # tiene sentido escribir el mismo artículo dos veces en el
                    # mismo Bronze. La deduplicación entre lotes ya la hace el
                    # guid en Silver.
                    clave = str(crudo.get("id") or crudo.get("link") or "")
                    if clave and clave in vistos:
                        continue
                    if clave:
                        vistos.add(clave)
                    registros.append(crudo)
                    nuevos += 1

                if nuevos == 0:
                    fallidas.append(f"{consulta.etiqueta}: sin entradas nuevas")
            except Exception as exc:  # noqa: BLE001
                fallidas.append(f"{consulta.etiqueta}: {type(exc).__name__}")

            if i < len(consultas) - 1:
                time.sleep(PAUSA)

    if not registros:
        return ResultadoFuente(
            source=SOURCE_ID,
            categoria="news",
            error=f"ninguna consulta devolvió entradas ({'; '.join(fallidas)})"[:500],
        )
    return ResultadoFuente(source=SOURCE_ID, categoria="news", registros=registros)


def _entrada_a_dict(entrada: Any, consulta: Consulta) -> dict[str, Any]:
    """Entrada de feedparser → dict serializable, sin limpieza."""
    from src.sources.rss import _serializable

    crudo = {k: v for k, v in dict(entrada).items() if not k.startswith("_")}

    # El medio publicador antes de que `source` se sobrescriba con el id de la
    # fuente de ingesta. Se pierde si no se rescata aquí.
    medio = crudo.get("source")
    if isinstance(medio, dict):
        crudo["medio_original"] = medio.get("title")
        crudo["medio_url"] = medio.get("href")

    crudo["source"] = SOURCE_ID
    crudo["_consulta"] = consulta.etiqueta
    return _serializable(crudo)
