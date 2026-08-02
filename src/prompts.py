"""Prompts del enriquecimiento NLP (PRD §6.4).

Dos pasadas, como especifica el PRD §4.4: NER + sentimiento, y M&A + fintech.
Separarlas no es capricho — pedir seis cosas en una sola respuesta degrada la
calidad de todas en un modelo de 9B, y además permite reintentar solo la mitad
que falló.

Principio de diseño: **vocabulario cerrado**. En las primeras pruebas el modelo
inventó el ticker "BANR" para Banorte. Todo campo que después vaya a hacer JOIN
—tickers, sectores— se enumera explícitamente en el prompt y se vuelve a
validar en el código; el modelo elige de una lista, no escribe libremente.

El catálogo de emisoras incluye **el símbolo y sus nombres comerciales**. Con
solo los símbolos, el modelo devolvía siempre lista vacía: no tiene por qué
saber que `GFNORTEO.MX` es Banorte o que `FEMSAUBD.MX` cubre a Oxxo. Enumerar
los alias convierte la tarea de recordar en la de reconocer.

Los prompts están en español porque el corpus es español (invariante 8) y
porque pedir en inglés sobre texto en español añade una traducción implícita
que el modelo hace mal con jerga financiera mexicana.
"""

from __future__ import annotations

SISTEMA_NER = """\
Eres un analista financiero experto en el mercado mexicano. Extraes entidades y \
evalúas el tono de noticias en español.

Respondes SIEMPRE con un único objeto JSON válido, sin texto adicional, sin \
explicaciones y sin bloques de código.

Esquema exacto de la respuesta:
{
  "tickers":   [string],   // SOLO de la lista permitida; [] si ninguna aplica
  "personas":  [string],   // nombres propios de personas mencionadas
  "empresas":  [string],   // organizaciones mencionadas, con su nombre habitual
  "sectores":  [string],   // SOLO de la lista permitida; [] si ninguno aplica
  "sentimiento": "positive" | "negative" | "neutral",
  "confianza_sentimiento": number  // entre 0.0 y 1.0
}

Reglas:
- Un ticker solo se incluye si la noticia trata SOBRE esa emisora, no si la \
menciona de pasada.
- El sentimiento es el del impacto ESPERADO EN EL MERCADO, no el tono \
periodístico: un despido masivo puede ser positivo para la acción.
- Si la noticia no es financiera, devuelve listas vacías y sentimiento neutral \
con confianza baja.
"""

USUARIO_NER = """\
EMISORAS PERMITIDAS. Usa exactamente el símbolo de la izquierda; a la derecha \
están los nombres con los que la noticia puede referirse a cada una:
{tickers_permitidos}

SECTORES PERMITIDOS (usa exactamente estas cadenas):
{sectores_permitidos}

NOTICIA
Titular: {titulo}
Cuerpo: {cuerpo}
"""


SISTEMA_MA = """\
Eres un analista de fusiones y adquisiciones especializado en el sector \
financiero mexicano y en la competencia entre banca tradicional y fintechs.

Respondes SIEMPRE con un único objeto JSON válido, sin texto adicional, sin \
explicaciones y sin bloques de código.

Esquema exacto de la respuesta:
{
  "es_evento_ma":  boolean,
  "tipo_ma":       "acquisition" | "merger" | "partnership" | "none",
  "confianza_ma":  number,        // entre 0.0 y 1.0
  "menciona_fintech": boolean,
  "fintechs":      [string],      // SOLO de la lista permitida
  "bancos_tradicionales": [string],
  "sector_afectado": string | null  // SOLO de la lista permitida
}

Reglas:
- "es_evento_ma" es true solo ante una operación CONCRETA: adquisición, fusión \
o alianza estratégica anunciada. Un rumor o un análisis especulativo NO cuenta.
- Si "es_evento_ma" es false, "tipo_ma" debe ser "none".
- "sector_afectado" es el segmento del mercado mexicano que se ve impactado.
- REGLA OBLIGATORIA: si "fintechs" NO está vacío, "sector_afectado" NO puede \
ser null. Ninguna de esas fintechs cotiza en la Bolsa Mexicana de Valores, así \
que el sector es el ÚNICO camino para medir su impacto sobre las emisoras que \
sí cotizan. Elige el sector del producto o servicio del que habla la noticia: \
una tarjeta de crédito es "banca_consumo", una cuenta de ahorro es \
"captacion_ahorro", una terminal de pago es "pagos_digitales".
- Si nada aplica, usa false, "none", listas vacías y null.
"""

USUARIO_MA = """\
FINTECHS CONOCIDAS (usa exactamente estas cadenas):
{fintechs_permitidas}

SECTORES PERMITIDOS PARA sector_afectado (usa exactamente estas cadenas):
{sectores_permitidos}

NOTICIA
Titular: {titulo}
Cuerpo: {cuerpo}
"""

# El cuerpo se recorta antes de enviarlo: 3000 caracteres cubren de sobra la
# entradilla y los primeros párrafos, que es donde está la información en el
# periodismo, y mantienen el prompt dentro de la ventana de contexto de 4096
# tokens que `lab-ollama` tiene configurada para este modelo.
LIMITE_CUERPO_PROMPT = 3000
