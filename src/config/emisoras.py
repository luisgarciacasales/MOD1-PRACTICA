"""Alias de emisoras de la BMV y léxico sectorial (soporte de la etapa validate).

Por qué existe: los feeds RSS entregan titular y resumen, **nunca un campo
ticker**. El contrato Silver exige al menos un Ticker, Sector o Entidad
identificable (PRD §6.2) y el PRD describe `tickers` como "extraídos de la
fuente" — pero la fuente los trae dentro del texto, no etiquetados.

Esta tabla permite esa extracción léxica en tiempo de validación. **No sustituye
al NER**: el enriquecimiento con el LLM (etapa `enrich`) escribe sus propios
`ner_tickers` en Gold con contexto semántico. Aquí solo se resuelve la pregunta
binaria "¿esta noticia es identificable?", que es la que decide entre Silver y
cuarentena. Un falso negativo aquí manda a `silver_dead_letters` una noticia
recuperable, así que conviene ser generoso con los alias.

Los alias se comparan normalizados (minúsculas, sin acentos) y con fronteras de
palabra, para que "amx" no coincida dentro de "amxico".
"""

# Ticker de Yahoo (con serie accionaria, ver ADR-11) → formas en que el texto
# puede nombrar a la emisora.
ALIAS_EMISORAS: dict[str, tuple[str, ...]] = {
    "GFNORTEO.MX": ("banorte", "grupo financiero banorte", "gfnorte", "gfnorteo"),
    "BBAJIOO.MX": ("banbajio", "banco del bajio", "bbajio", "bbajioo"),
    "WALMEX.MX": ("walmex", "walmart de mexico", "walmart mexico", "bodega aurrera", "sam's club"),
    "AMXB.MX": ("america movil", "amx", "amxb", "telcel", "telmex"),
    "GMEXICOB.MX": ("grupo mexico", "gmexico", "gmexicob", "southern copper", "ferromex"),
    "CEMEXCPO.MX": ("cemex", "cemexcpo"),
    "FEMSAUBD.MX": ("femsa", "femsaubd", "oxxo", "coca-cola femsa"),
    "ALSEA.MX": ("alsea", "domino's", "starbucks mexico", "burger king mexico"),
}

# Instituciones financieras que no están en el universo de 8 emisoras pero cuya
# mención hace la noticia relevante para el sector. Se registran como
# **entidades**, no como tickers: no cotizan en el universo del proyecto y
# fingir un ticker aquí contaminaría el JOIN con precios.
ENTIDADES_FINANCIERAS: tuple[str, ...] = (
    "banxico", "banco de mexico", "bbva mexico", "santander mexico", "citibanamex",
    "banamex", "hsbc mexico", "scotiabank", "banco azteca", "inbursa", "banregio",
    "afirme", "compartamos", "bancoppel", "condusef", "cnbv", "shcp", "hacienda",
    "buro de credito", "fitch", "moody's", "s&p global", "hr ratings",
)

# Léxico sectorial: si no hay ticker ni entidad, un sector identificable también
# satisface el contrato. Los nombres coinciden con las claves de SECTOR_A_PROXY
# donde aplica, para que el proxy ticker pueda resolverse aguas abajo.
LEXICO_SECTORES: dict[str, tuple[str, ...]] = {
    "banca_consumo": ("credito al consumo", "tarjeta de credito", "credito personal",
                      "banca de consumo", "credito revolvente"),
    "captacion_ahorro": ("captacion", "cuenta de ahorro", "deposito a plazo",
                         "pagare bancario", "ahorro voluntario"),
    "credito_automotriz": ("credito automotriz", "financiamiento automotriz",
                           "credito para auto"),
    "pagos_digitales": ("pagos digitales", "terminal punto de venta", "tpv",
                        "transferencia electronica", "codi", "dimo", "spei"),
    "insurtech": ("seguros", "aseguradora", "poliza", "insurtech"),
    "banca": ("banca multiple", "sistema financiero", "institucion de banca",
              "cartera vencida", "indice de morosidad", "margen financiero"),
    "bursatil": ("bolsa mexicana de valores", "bmv", "biva", "indice de precios",
                 "s&p/bmv ipc", "emisora", "oferta publica"),
}
