"""Qué series macro viajan en el `macro_context` de cada correlación.

El problema que resuelve: `gold_news_market_corr.macro_context` es un JSONB que
se escribe en **cada fila**. Con 17 series macro, cada correlación arrastraría un
objeto de 17 entradas y la mayoría sería irrelevante para la noticia concreta —
la tabla engorda con ruido y la lectura de una fila se vuelve incómoda.

La solución no es guardar menos: **todas las series siguen en
`gold_macro_indicators` y siguen consultables**. Solo se acota lo que se
materializa junto a cada correlación.

Criterio de selección: las cinco variables que un analista mira para leer una
noticia bancaria, más la pendiente de la curva que se deriva de dos de ellas.
Nada de series mensuales rezagadas —el IGAE va dos meses atrás— ni redundantes:
la tasa objetivo y el fondeo bancario valen lo mismo hoy (6,50) y basta una.
"""

from __future__ import annotations

# Series que se materializan en cada `macro_context`.
SERIES_EN_CONTEXTO: tuple[str, ...] = (
    "SF61745",   # Tasa objetivo de Banxico — postura de política monetaria
    "SF43783",   # TIIE a 28 días — referencia de casi todo el crédito bancario
    "SF43936",   # Cetes a 28 días — tramo corto de la curva
    "SF43945",   # Cetes a 364 días — tramo largo de la curva
    "SF43718",   # Tipo de cambio FIX USD/MXN
    "SP74625",   # INPC subíndice subyacente — inflación núcleo
)

# Extremos con los que se calcula la pendiente. Se materializa como valor
# derivado porque es lo que de verdad se interpreta: la diferencia entre prestar
# a un año y fondearse a un mes es, en primera aproximación, el margen del banco.
CURVA_CORTO = "SF43936"
CURVA_LARGO = "SF43945"
