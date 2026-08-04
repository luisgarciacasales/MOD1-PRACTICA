"""Indicadores del INEGI vía su API de Indicadores (ampliación 2026-08-04).

Los IDs viven en `src/config/inegi_series.py`, que documenta por qué hay que
obtenerlos a mano desde la consola web: la API no tiene catálogo consultable y
sus respuestas no incluyen el nombre del indicador.

Formato de la respuesta, verificado contra la API real:

    {"Header": {...},
     "Series": [{"INDICADOR": "1002000001", "FREQ": "4", "UNIT": "3", ...,
                 "OBSERVATIONS": [{"TIME_PERIOD": "2026/01",
                                   "OBS_VALUE": "19.70122577228288375360",
                                   "OBS_EXCEPTION": null, ...}]}]}

`TIME_PERIOD` viene como `aaaa/mm` en las series mensuales y como `aaaa` en las
anuales; `OBS_VALUE` es cadena con muchos decimales. Bronze lo guarda tal cual y
la conversión ocurre en `validate`, igual que con BANXICO.
"""

from __future__ import annotations

import time
from typing import Any

from src.config import get_settings
from src.config.inegi_series import INDICADORES, IndicadorInegi, url_de
from src.sources.base import ResultadoFuente
from src.sources.http import sesion_cacheada

TIMEOUT = 30
PAUSA = 0.5

SOURCE_ID = "inegi"


def ingerir(
    *, indicadores: tuple[IndicadorInegi, ...] = INDICADORES
) -> ResultadoFuente:
    """Descarga los indicadores configurados. Fail-soft por indicador y por fuente."""
    settings = get_settings()

    if not settings.inegi_token or settings.inegi_token == "CAMBIAME":
        # Chequeo explícito, como en BANXICO: sin él la API devuelve un 400 con
        # "No se encontraron resultados", que no menciona el token en absoluto.
        return ResultadoFuente(
            source=SOURCE_ID,
            categoria="market",
            error=(
                "INEGI_TOKEN no configurado en el .env de mi-pc. "
                "Regístrate gratis en "
                "https://www.inegi.org.mx/servicios/api_indicadores.html"
            ),
        )

    if not indicadores:
        # No es un fallo técnico sino de configuración pendiente, y conviene que
        # el mensaje lo diga: la API no permite descubrir los IDs, hay que
        # obtenerlos de la consola web. Ver src/config/inegi_series.py.
        return ResultadoFuente(
            source=SOURCE_ID,
            categoria="market",
            error=(
                "sin indicadores configurados — los IDs se obtienen a mano en "
                "https://www.inegi.org.mx/app/indicadores/ y se añaden a "
                "src/config/inegi_series.py con su nombre confirmado"
            ),
        )

    registros: list[dict[str, Any]] = []
    fallidos: list[str] = []

    for i, indicador in enumerate(indicadores):
        try:
            # TTL semanal: los indicadores del INEGI son mensuales o
            # trimestrales, así que consultarlos a diario no aporta nada.
            with sesion_cacheada(
                settings.cache_ttl_macro_seconds, nombre="inegi"
            ) as sesion:
                respuesta = sesion.get(
                    url_de(indicador.id, settings.inegi_token),
                    headers={"Accept": "application/json"},
                    timeout=TIMEOUT,
                )
                respuesta.raise_for_status()
                cuerpo = respuesta.json()

            filas = _aplanar(cuerpo, indicador)
            if not filas:
                raise ValueError("el indicador no trae observaciones")
            registros.extend(filas)
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{indicador.id}: {type(exc).__name__}")

        if i < len(indicadores) - 1:
            time.sleep(PAUSA)

    if not registros:
        return ResultadoFuente(
            source=SOURCE_ID,
            categoria="market",
            error=f"ningún indicador devolvió datos ({'; '.join(fallidos)})"[:500],
        )
    return ResultadoFuente(source=SOURCE_ID, categoria="market", registros=registros)


def _aplanar(cuerpo: dict[str, Any], indicador: IndicadorInegi) -> list[dict[str, Any]]:
    """`Series[].OBSERVATIONS[]` → filas. Conversión de formato, no limpieza:
    los valores se copian tal cual, incluidos los `OBS_EXCEPTION` que marcan
    dato no disponible."""
    filas: list[dict[str, Any]] = []
    for serie in cuerpo.get("Series", []) or []:
        for obs in serie.get("OBSERVATIONS", []) or []:
            filas.append({
                "indicador_id": serie.get("INDICADOR", indicador.id),
                "indicador_nombre": indicador.nombre,
                "frecuencia": indicador.frecuencia,
                "periodo": obs.get("TIME_PERIOD"),
                "valor": obs.get("OBS_VALUE"),
                "excepcion": obs.get("OBS_EXCEPTION"),
                "ultima_actualizacion": serie.get("LASTUPDATE"),
                "source": SOURCE_ID,
            })
    return filas
