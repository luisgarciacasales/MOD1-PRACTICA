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

  área    `00`         Nacional. Con `0700` la API devuelve 400 — fue la causa
                       de los primeros fallos.
  fuente  `BIE-BISE`   **Valor literal combinado.** Ni `BIE` ni `BISE` por
                       separado funcionan para las series económicas: `BIE`
                       devuelve 400 en todos los IDs y `BISE` solo responde a
                       los censales. Toda la documentación del INEGI usa
                       ejemplos con `BISE`, lo que llevó a descartar la
                       combinación durante horas. La aportó el usuario desde la
                       consola web, y con ella el IGAE responde a la primera.

## Señales para validar un indicador nuevo

`FREQ = 8` corresponde a las series mensuales, y eso sí es consistente en los
tres indicadores confirmados.

El campo `UNIT`, en cambio, **no sirve como atajo**. Tras dos indicadores parecía
que `1051` era índice y `3` tasa porcentual, pero la confianza del consumidor
devolvió `1014` —otro índice con otro código—. `UNIT` remite a un catálogo que la
API no expone, así que sus valores no se pueden interpretar: son etiquetas
opacas. La única validación fiable sigue siendo **mirar el rango de valores y la
periodicidad** y comprobar que encajan con lo que se supone que mide.

Las consultas **múltiples** funcionan separando IDs por coma
(`.../INDICATOR/737121,444603/...`). Aquí se hace una petición por indicador a
propósito: así el fail-soft es por indicador y el caché se invalida por separado.

## Formatos de `TIME_PERIOD`

Varían con la frecuencia, y confundirlos corrompe la serie:

    anual       `2020`          → se ancla al 1 de enero
    mensual     `2026/05`       → se ancla al día 1 del mes
    quincenal   `2026/06/02`    → el tercer segmento es la QUINCENA (01 o 02)

El caso quincenal estuvo a punto de costar la mitad del INPC: ignorar el tercer
segmento hace que ambas quincenas de un mes caigan en el mismo día, y con la
clave única `(series_id, date)` la segunda sobrescribe a la primera sin error.
Se mapea la quincena 01 al día 1 y la 02 al día 16, que es la convención
habitual y preserva orden y unicidad.

## Orden de las observaciones

La API devuelve `OBSERVATIONS` **de más reciente a más antigua**. No se reordena
en la ingesta —Bronze conserva lo recibido— pero quien las consuma no debe
suponer orden ascendente.
"""

from __future__ import annotations

from typing import Literal, NamedTuple

Frecuencia = Literal["quincenal", "mensual", "trimestral", "anual"]

# Ruta verificada de la API de Indicadores v2.0.
BASE_URL = "https://www.inegi.org.mx/app/api/indicadores/desarrolladores/jsonxml/INDICATOR"
AREA_NACIONAL = "00"
FUENTE = "BIE-BISE"


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
INDICADORES: tuple[IndicadorInegi, ...] = (
    # Confirmado el 2026-08-05: 401 observaciones mensuales de 1993/01 a
    # 2026/05, índice base 100 que pasa de 55,4 en 1993 a 108,6 en 2026.
    # Es el proxy mensual del PIB — el PIB trimestral llega demasiado tarde para
    # explicar el movimiento de una acción.
    IndicadorInegi("737121", "IGAE — Indicador Global de Actividad Económica", "mensual"),
    # Confirmado el 2026-08-05: 258 observaciones mensuales de 2005/01 a 2026/06,
    # valores entre 2,22 y 6,34 — inequívocamente una tasa porcentual (UNIT=3).
    # Último dato 2,90% en junio de 2026.
    # Es el indicador que anticipa la morosidad de la cartera de consumo, el
    # terreno donde compiten los neobancos con la banca tradicional.
    IndicadorInegi("444603", "Tasa de desocupación (ENOE)", "mensual"),
    # Confirmado el 2026-08-05: 304 observaciones mensuales de 2001/04 a 2026/07,
    # valores entre 28,67 y 48,90. Coherente con el Índice de Confianza del
    # Consumidor, que en México oscila en la banda 30-50 y NO está centrado en
    # 100. Último dato 45,08 en julio de 2026.
    # Es el indicador macro MÁS OPORTUNO del conjunto: llega hasta julio cuando
    # el IGAE solo alcanza mayo, así que es el que antes refleja un cambio de
    # ánimo en la demanda de crédito al consumo.
    IndicadorInegi("454168", "Índice de Confianza del Consumidor (ENCO)", "mensual"),
    # Confirmado el 2026-08-05: 925 observaciones QUINCENALES de 1988/01/01 a
    # 2026/07/01, índice que va de 4,64 a 145,86 — el INPC general con base
    # 2018=100. Último dato 145,091.
    # Complementa al subyacente de BANXICO (SP74625): el general incluye
    # energéticos y agropecuarios, mucho más volátiles, y su frecuencia
    # quincenal lo hace el dato de inflación más oportuno disponible.
    IndicadorInegi("910420", "INPC general (quincenal)", "quincenal"),
)

INDICADORES_POR_ID: dict[str, IndicadorInegi] = {i.id: i for i in INDICADORES}


def url_de(indicador_id: str, token: str) -> str:
    """URL de consulta de un indicador. `false` = serie histórica completa, no
    solo el dato más reciente."""
    return (
        f"{BASE_URL}/{indicador_id}/es/{AREA_NACIONAL}/false/{FUENTE}/2.0/{token}"
        "?type=json"
    )
