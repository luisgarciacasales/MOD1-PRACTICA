"""Indicadores del INEGI (fuente añadida el 2026-08-04).

Aporta el contexto de **actividad económica real** que BANXICO no publica y que
es el motor de fondo del riesgo de crédito bancario: el desempleo y la confianza
del consumidor anticipan la morosidad de la cartera de consumo, que es justo el
terreno donde compiten los neobancos.

## Cómo obtener un ID de indicador

La API **no** permite descubrirlos. Verificado el 2026-08-04:

· No hay catálogo consultable: `CL_INDICATOR` devuelve HTML o 405.
· La respuesta de un indicador **no incluye su nombre**, solo códigos opacos
  (`TOPIC`, `UNIT`, `FREQ`, `SOURCE`) que remiten a catálogos no expuestos.
· Las páginas temáticas de inegi.org.mx son cáscaras de JavaScript sin IDs.

Por eso los IDs se obtienen a mano, una vez, desde la consola web:

  1. Abrir https://www.inegi.org.mx/app/indicadores/
  2. Navegar al indicador deseado y seleccionarlo.
  3. La consola ofrece "Consultar por API" / muestra el ID numérico; también
     aparece en la URL de la consulta generada.
  4. Añadirlo abajo con su nombre y frecuencia REALES, no supuestos.

**Nunca añadas un ID sin haber confirmado qué mide.** Con BANXICO ocurrió que
cuatro de los seis IDs del PRD apuntaban a series distintas de lo que decía el
documento, y el fallo era silencioso porque el dato era numéricamente válido:
solo estaba mal nombrado. Un indicador mal etiquetado sobrevive a toda la
validación y envenena la interpretación.

## Parámetros de la ruta, ya verificados

  área    `00`     Nacional. Con `0700` la API devuelve 400 — fue la causa de
                   los primeros fallos.
  fuente  `BISE`   Es la que responde. `BIE` devolvió 400 en todos los IDs
                   probados, pese a ser el banco de series económicas.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

Frecuencia = Literal["mensual", "trimestral", "anual"]

# Ruta verificada de la API de Indicadores v2.0.
BASE_URL = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR"
AREA_NACIONAL = "00"
FUENTE = "BISE"


class IndicadorInegi(NamedTuple):
    id: str
    nombre: str
    frecuencia: Frecuencia


# ---------------------------------------------------------------------------
# Catálogo. VACÍO a propósito: ningún ID entra aquí sin confirmar qué mide.
#
# Los que aportarían al objetivo del proyecto, por orden de valor:
#
#   IGAE                        proxy mensual del PIB
#   Tasa de desocupación        ENOE; anticipa morosidad de consumo
#   Confianza del consumidor    anticipa demanda de crédito
#   INPC general                inflación desde su fuente original
#   Actividad industrial        sensibilidad de la cartera empresarial
#
# Ejemplo de cómo se añade uno ya confirmado:
#     IndicadorInegi("628194", "IGAE", "mensual"),
# ---------------------------------------------------------------------------
INDICADORES: tuple[IndicadorInegi, ...] = ()

INDICADORES_POR_ID: dict[str, IndicadorInegi] = {i.id: i for i in INDICADORES}


def url_de(indicador_id: str, token: str) -> str:
    """URL de consulta de un indicador. `false` = serie histórica completa, no
    solo el dato más reciente."""
    return (
        f"{BASE_URL}/{indicador_id}/es/{AREA_NACIONAL}/false/{FUENTE}/2.0/{token}"
        "?type=json"
    )
