"""Léxico para detectar noticias macroeconómicas (bypass del PRD §6.2).

Por qué existe: el contrato Silver exige al menos un Ticker, Sector o Entidad.
Una nota sobre "Banxico sube la tasa objetivo 25 puntos base" no menciona
ninguna emisora y, sin excepción, se iría a cuarentena — borrando justo el
contexto macro que da sentido al resto del corpus.

El PRD detecta estas noticias "por el LLM o por source = bloomberg". El LLM
todavía no ha corrido cuando validamos, así que aquí vive la señal léxica que
permite aplicar el bypass en tiempo de validación. La etapa de enriquecimiento
puede corregir el flag después, con más contexto.
"""

# Términos en minúsculas y SIN acentos: la comparación normaliza ambos lados,
# porque los medios mexicanos escriben "inflacion" e "inflación" indistintamente.
LEXICO_MACRO: frozenset[str] = frozenset({
    # Política monetaria
    "banxico",
    "banco de mexico",
    "junta de gobierno",
    "politica monetaria",
    "tasa de referencia",
    "tasa objetivo",
    "tasa de interes",
    "puntos base",
    "restriccion monetaria",
    "relajamiento monetario",
    # Tasas e indicadores
    "tiie",
    "cetes",
    "bonos m",
    "curva de rendimientos",
    # Inflación
    "inflacion",
    "inpc",
    "subyacente",
    "canasta basica",
    # Tipo de cambio y agregados
    "tipo de cambio",
    "peso mexicano",
    "usd/mxn",
    "agregado monetario",
    # Actividad económica
    "producto interno bruto",
    "pib",
    "igae",
    # Actores externos con efecto directo en la política local
    "reserva federal",
    "fed",
    "fomc",
})

# Mínimo de términos distintos que deben aparecer para considerar la noticia
# macro. Con 1 solo, cualquier mención de pasada a "el peso mexicano" activaría
# el bypass y el contrato dejaría de filtrar nada.
MIN_TERMINOS_MACRO: int = 2
