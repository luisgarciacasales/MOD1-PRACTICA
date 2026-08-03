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
SERIES: tuple[SerieBanxico, ...] = (
    SerieBanxico("SF43783", "TIIE a 28 días", "diaria"),
    SerieBanxico("SF63528", "Tasa de fondeo gubernamental (objetivo Banxico)", "diaria"),
    SerieBanxico("SF46410", "Tipo de cambio FIX USD/MXN", "diaria"),
    SerieBanxico("SF43718", "Tipo de cambio USD/MXN para liquidar obligaciones", "diaria"),
    SerieBanxico("SF311408", "Agregado monetario M1", "mensual"),
    SerieBanxico("SP74625", "INPC subíndice subyacente", "mensual"),
)

# Endpoint base del SIE. El token va en la cabecera 'Bmx-Token', no en la URL,
# para que no acabe registrado en logs de proxies o en el caché de requests.
SIE_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"

SERIES_POR_ID: dict[str, SerieBanxico] = {s.id: s for s in SERIES}
