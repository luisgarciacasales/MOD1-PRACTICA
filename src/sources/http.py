"""Sesiones HTTP con caché (PRD §6.7).

`requests-cache` con backend SQLite intercepta las llamadas repetidas a
yfinance y BANXICO. Durante el desarrollo iterativo se consulta el mismo ticker
o la misma serie decenas de veces; sin caché se agota la cuota o Yahoo empieza
a responder con throttling (riesgo nº7 del PRD §9).

TTL diferenciado: diario para mercado, semanal para series macro mensuales —
pedir cada día una serie que BANXICO publica una vez al mes es desperdicio.
"""

from __future__ import annotations

from pathlib import Path

import requests
import requests_cache

from src.config import get_settings

# Cabeceras de navegador. Varios medios mexicanos responden 403 a los
# User-Agent por defecto de las librerías HTTP.
CABECERAS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, application/json;q=0.9, */*;q=0.8",
    "Accept-Language": "es-MX,es;q=0.9,en;q=0.8",
}


def sesion_cacheada(ttl_segundos: int, *, nombre: str) -> requests_cache.CachedSession:
    """Sesión con caché SQLite. `nombre` separa los espacios de caché por uso."""
    settings = get_settings()
    ruta = Path(settings.requests_cache_path)
    ruta.parent.mkdir(parents=True, exist_ok=True)

    sesion = requests_cache.CachedSession(
        cache_name=str(ruta.with_suffix("")),
        backend="sqlite",
        expire_after=ttl_segundos,
        # Solo se cachean las respuestas buenas: guardar un 403 o un 500
        # convertiría un fallo transitorio en uno persistente durante el TTL.
        allowable_codes=(200,),
        stale_if_error=True,
        namespace=nombre,
    )
    sesion.headers.update(CABECERAS)
    return sesion


def sesion_simple() -> requests.Session:
    """Sin caché — para fuentes de noticias, donde el objetivo es justamente
    ver lo nuevo en cada corrida."""
    sesion = requests.Session()
    sesion.headers.update(CABECERAS)
    return sesion
