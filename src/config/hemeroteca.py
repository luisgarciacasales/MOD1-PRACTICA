"""Medios con archivo histórico recuperable, y cómo se recorre cada uno.

Existe porque el corpus tenía un hueco que ninguna fuente en vivo podía
llenar: el 9-sep-2026, de 1.767 noticias, 1.693 eran de los últimos 60 días —
setenta y cuatro cubrían ocho años. Sin historia no hay backtest de la señal de
noticias, y por eso F3 no pudo evaluarla.

Se evaluaron y descartaron GDELT (no menciona ni una vez a las emisoras del
universo, ver ADR-19), los agregadores de prensa (Factiva y Nexis prohíben el
uso automatizado en su licencia de lectura) y la lectura directa de los medios
(El Economista responde 403 desde datacenter, ADR-11). La vía que sí funciona
es el archivo del Internet Archive: tiene copia de los mismos medios y una API
de descubrimiento pública pensada para esto.

**El filtro por titular es lo que hace viable el volumen.** Las URLs de estos
medios llevan el titular y la fecha, así que se decide qué descargar sin
descargar nada: de 755 artículos de `/mercados/` en marzo de 2020, 125
mencionaban una emisora del universo. Un 17% — el resto no se pide siquiera.

Ese filtro sesga el corpus hacia noticias donde la emisora es PROTAGONISTA y no
una mención de pasada. Es deliberado: es el mismo criterio que ADR-17 adoptó
para las correlaciones cuando el veredicto del NER pasó a mandar sobre el
léxico.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MedioArchivado:
    """Un medio y las secciones suyas que vale la pena recorrer."""

    clave: str
    dominio: str
    secciones: tuple[str, ...]
    source: str
    # Cómo viene la fecha de publicación dentro de la URL. Se prefiere a
    # cualquier fecha del HTML: es la del propio identificador del artículo y
    # no depende de qué etiqueta traiga la plantilla ese año — el mismo
    # problema de lookahead que costó el bug de `reportes_ir` en agosto.
    patron_fecha: str = r"-(\d{8})-\d+\.html"


MEDIOS: dict[str, MedioArchivado] = {
    # Diario financiero mexicano. Es el que ADR-11 descartó por WAF y el único
    # medio de negocios que GDELT indexaba — aquí se recupera por otra vía.
    "eleconomista": MedioArchivado(
        clave="eleconomista",
        dominio="eleconomista.com.mx",
        secciones=("mercados", "empresas", "economia"),
        source="eleconomista_archivo",
    ),
    # Ya es fuente en vivo (`financiero`), pero su RSS solo da lo reciente; el
    # archivo aporta la historia que el feed nunca tuvo. Se carga bajo un
    # `source` distinto para no confundir lo recuperado con lo ingerido en su
    # momento.
    "elfinanciero": MedioArchivado(
        clave="elfinanciero",
        dominio="elfinanciero.com.mx",
        secciones=("mercados", "empresas", "economia"),
        source="elfinanciero_archivo",
    ),
}

FUENTES_ARCHIVO = frozenset(m.source for m in MEDIOS.values())
