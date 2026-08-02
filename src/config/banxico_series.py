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


SERIES: tuple[SerieBanxico, ...] = (
    SerieBanxico("SF43783", "TIIE a 28 días", "diaria"),
    SerieBanxico("SF63528", "Tasa de fondeo gubernamental (objetivo Banxico)", "diaria"),
    SerieBanxico("SF46410", "Tipo de cambio FIX USD/MXN", "diaria"),
    SerieBanxico("SF43718", "Tipo de cambio USD/MXN para liquidar obligaciones", "diaria"),
    SerieBanxico("SF617", "Agregado monetario M1", "mensual"),
    SerieBanxico("SF10770", "Inflación subyacente mensual (INPC)", "mensual"),
)

# Endpoint base del SIE. El token va en la cabecera 'Bmx-Token', no en la URL,
# para que no acabe registrado en logs de proxies o en el caché de requests.
SIE_BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"

SERIES_POR_ID: dict[str, SerieBanxico] = {s.id: s for s in SERIES}
