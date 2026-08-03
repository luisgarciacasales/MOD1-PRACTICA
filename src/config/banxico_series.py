"""Series del SIE de BANXICO ingeridas en Fase 1 (PRD §3.5).

La frecuencia importa para el caché: las series diarias se invalidan cada 24 h y
las mensuales cada semana (PRD §6.7). Ingerir una serie mensual todos los días
es correcto y barato — solo se escribe fila nueva si BANXICO publicó dato nuevo,
y el UPSERT por (series_id, date) garantiza idempotencia.
"""

from typing import Literal, NamedTuple

Frecuencia = Literal["diaria", "mensual"]


class SerieBanxico(NamedTuple):
    id: str
    nombre: str
    frecuencia: Frecuencia


# CORRECCIÓN RESPECTO AL PRD (verificado contra el SIE el 2026-08-03):
# los dos IDs mensuales del PRD §3.5 apuntan a series que no son lo que dice el
# documento y que además están discontinuadas — devuelven 0 puntos en los
# últimos dos años, así que la capa macro se quedaba solo con las 4 diarias:
#
#   SF617    el PRD dice "Agregado monetario M1"        → es "BANCA NACIONAL
#            RECURSOS MONEDA EXTRANJERA". El M1 real es SF311408.
#   SF10770  el PRD dice "Inflación subyacente (INPC)"  → es "BANCO DE MEXICO
#            RECURSOS TOTALES". El subíndice subyacente real es SP74625.
#
# SP74625 es un índice de NIVEL, no una tasa. Es lo correcto aquí: `transform`
# calcula `yoy_change_pct` sobre él, y esa variación interanual ES la inflación
# subyacente que pide el PRD.
#   SF63528  el PRD dice "Tasa de fondeo / objetivo"    → es "Serie histórica
#            del tipo de cambio peso dólar" (17,33). La tasa objetivo real es
#            SF61745 (6,50).
#   SF46410  el PRD dice "Tipo de cambio FIX"           → es "Cotización de las
#            divisas que conforman la canasta del DEG" (19,97), que no es
#            USD/MXN. El FIX es SF43718, ya presente en la lista, así que su
#            hueco se aprovecha para la tasa de fondeo bancario (SF43773).
#
# Consecuencia de no corregirlo: el `macro_context` de cada correlación
# etiquetaba un tipo de cambio como "tasa de fondeo" y una canasta del DEG como
# "FIX". El dato existía y era numéricamente válido, así que ningún contrato lo
# rechazaba — solo estaba mal nombrado, que es la clase de error que sobrevive a
# la validación y envenena la interpretación.
SERIES: tuple[SerieBanxico, ...] = (
    # --- Tasas ---
    SerieBanxico("SF43783", "TIIE a 28 días", "diaria"),
    SerieBanxico("SF61745", "Tasa objetivo de Banxico", "diaria"),
    SerieBanxico("SF43773", "Tasa de fondeo bancario (mediana ponderada)", "diaria"),
    # --- Tipo de cambio ---
    SerieBanxico("SF43718", "Tipo de cambio FIX USD/MXN", "diaria"),
    # --- Mensuales ---
    SerieBanxico("SF311408", "Agregado monetario M1", "mensual"),
    SerieBanxico("SP74625", "INPC subíndice subyacente", "mensual"),
)

# Endpoint base del SIE. El token va en la cabecera 'Bmx-Token', no en la URL,
# para que no acabe registrado en logs de proxies o en el caché de requests.
SIE_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"

SERIES_POR_ID: dict[str, SerieBanxico] = {s.id: s for s in SERIES}
