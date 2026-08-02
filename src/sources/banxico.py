"""Series macroeconómicas del SIE de BANXICO (PRD §3.5).

El token va en la cabecera `Bmx-Token`, nunca en la URL: la URL acaba en logs
de proxies y en las claves del caché de `requests-cache`, y un token en claro
ahí es una fuga silenciosa.

El caché sí funciona en esta fuente (HTTP plano vía requests): TTL semanal para
las series mensuales, diario para las diarias — pedir cada día una serie que
BANXICO publica una vez al mes es desperdicio puro.
"""

from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.config.banxico_series import SERIES, SIE_BASE_URL, SerieBanxico
from src.sources.base import ResultadoFuente
from src.sources.http import sesion_cacheada

TIMEOUT = 25


def ingerir(*, series: tuple[SerieBanxico, ...] = SERIES) -> ResultadoFuente:
    """Descarga las series configuradas. Fail-soft por serie y por fuente."""
    settings = get_settings()

    if not settings.banxico_token or settings.banxico_token == "CAMBIAME":
        # Chequeo previo explícito: sin él, la API responde 400 y el mensaje
        # real ("Bad Request") no dice nada sobre la causa.
        return ResultadoFuente(
            source="banxico",
            categoria="market",
            error=(
                "BANXICO_TOKEN no configurado en el .env de mi-pc. "
                "Regístrate gratis en https://www.banxico.org.mx/SieAPIRest/"
            ),
        )

    registros: list[dict[str, Any]] = []
    fallidas: list[str] = []

    for serie in series:
        ttl = (
            settings.cache_ttl_macro_seconds
            if serie.frecuencia == "mensual"
            else settings.cache_ttl_market_seconds
        )
        try:
            with sesion_cacheada(ttl, nombre=f"banxico_{serie.frecuencia}") as sesion:
                respuesta = sesion.get(
                    f"{SIE_BASE_URL}/{serie.id}/datos/oportuno",
                    headers={"Bmx-Token": settings.banxico_token},
                    timeout=TIMEOUT,
                )
                respuesta.raise_for_status()
                cuerpo = respuesta.json()

            registros.extend(_aplanar(cuerpo, serie))
        except Exception as exc:  # noqa: BLE001
            fallidas.append(f"{serie.id}: {type(exc).__name__}")

    if not registros:
        return ResultadoFuente(
            source="banxico",
            categoria="market",
            error=f"ninguna serie devolvió datos ({'; '.join(fallidas)})"[:500],
        )
    return ResultadoFuente(source="banxico", categoria="market", registros=registros)


def _aplanar(cuerpo: dict[str, Any], serie: SerieBanxico) -> list[dict[str, Any]]:
    """El SIE anida `bmx.series[].datos[]`. Se aplana a filas (PRD §3.5).

    Aplanar una estructura anidada es conversión de formato, no limpieza: los
    valores se copian tal cual, incluido el `dato` como string —BANXICO usa
    "N/E" para los no disponibles, y convertirlo aquí sería transformar—.
    """
    filas: list[dict[str, Any]] = []
    for s in cuerpo.get("bmx", {}).get("series", []):
        for punto in s.get("datos", []) or []:
            filas.append({
                "series_id": s.get("idSerie", serie.id),
                "series_name": serie.nombre,
                "frecuencia": serie.frecuencia,
                "fecha": punto.get("fecha"),
                "dato": punto.get("dato"),
                "source": "banxico",
            })
    return filas
