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
    # --- Banca y servicios financieros ---
    "GFNORTEO.MX": ("banorte", "grupo financiero banorte", "gfnorte", "gfnorteo"),
    "BBAJIOO.MX": ("banbajio", "banco del bajio", "bbajio", "bbajioo"),
    "GENTERA.MX": ("gentera", "compartamos", "compartamos banco", "banco compartamos"),
    # OJO: "regional" a secas NO es alias. Es un adjetivo corrísimo en español
    # ("banca regional", "mercado regional") y produciría falsos positivos en
    # casi cualquier noticia económica.
    # "hey banco" SÍ lo es: Hey Banco es el banco digital de Banregio, así que
    # una noticia sobre Hey es una noticia sobre el instrumento cotizado RA.MX.
    # Eso lo convierte en correlación DIRECTA, mejor que un proxy sectorial.
    "RA.MX": ("banregio", "grupo financiero banregio", "hey banco", "heybanco"),
    "GFINBURO.MX": ("inbursa", "banco inbursa", "grupo financiero inbursa", "gfinbur"),
    # PROBLEMA CONOCIDO de esta emisora: su nombre coincide con el del propio
    # mercado. "la BMV cerró al alza" habla del índice, no de las acciones de la
    # operadora, y aceptar "bmv" o "bolsa mexicana de valores" como alias
    # etiquetaría con BOLSAA.MX casi toda noticia bursátil. Se usan solo formas
    # que se refieren sin ambigüedad a la empresa. El coste es cobertura baja;
    # la alternativa era ruido masivo. (Nota: "bmv" sí es término del léxico
    # SECTORIAL de abajo, que es un campo distinto y ahí sí es correcto.)
    "BOLSAA.MX": ("grupo bmv", "bolsa mexicana de valores s.a.b", "bolsaa"),
    "Q.MX": ("qualitas", "qualitas controladora", "quálitas"),
    # --- Resto del universo ---
    "WALMEX.MX": ("walmex", "walmart de mexico", "walmart mexico", "bodega aurrera", "sam's club"),
    "AMXB.MX": ("america movil", "amx", "amxb", "telcel", "telmex"),
    "GMEXICOB.MX": ("grupo mexico", "gmexico", "gmexicob", "southern copper", "ferromex"),
    "CEMEXCPO.MX": ("cemex", "cemexcpo"),
    "FEMSAUBD.MX": ("femsa", "femsaubd", "oxxo", "coca-cola femsa"),
    "ALSEA.MX": ("alsea", "domino's", "starbucks mexico", "burger king mexico"),
}

# Instituciones y organismos financieros que NO están en el universo cotizado
# del proyecto, pero cuya mención hace la noticia relevante para el sector. Se
# registran como **entidades**, no como tickers: fingir un ticker aquí
# contaminaría el JOIN con precios.
#
# Al ampliar el universo el 2026-08-03 se retiraron de esta lista `inbursa`,
# `banregio` y `compartamos`: ahora cotizan (GFINBURO.MX, RA.MX, GENTERA.MX) y
# tenerlas en los dos sitios las habría etiquetado a la vez como emisora y como
# entidad sin cotización, que es contradictorio.
ENTIDADES_FINANCIERAS: tuple[str, ...] = (
    "banxico", "banco de mexico", "bbva mexico", "santander mexico", "citibanamex",
    "banamex", "hsbc mexico", "scotiabank", "banco azteca",
    "afirme", "bancoppel", "banco del bienestar", "nafin", "bancomext",
    "condusef", "cnbv", "shcp", "hacienda", "ipab",
    "buro de credito", "fitch", "moody's", "s&p global", "hr ratings",
)

# Léxico sectorial: si no hay ticker ni entidad, un sector identificable también
# satisface el contrato. Los nombres coinciden con las claves de SECTOR_A_PROXY
# donde aplica, para que el proxy ticker pueda resolverse aguas abajo.
#
# El ORDEN importa: `extraer_sector` devuelve la primera coincidencia, y los
# sectores específicos van antes que los genéricos porque son los únicos que
# permiten resolver un proxy ticker. Etiquetar como "banca" una nota que habla
# de crédito al consumo perdería esa capacidad.
#
# Los plurales se listan explícitamente: la comparación usa fronteras de
# palabra, así que "credito al consumo" NO coincide con "créditos al consumo".
LEXICO_SECTORES: dict[str, tuple[str, ...]] = {
    "banca_consumo": ("credito al consumo", "creditos al consumo",
                      "tarjeta de credito", "tarjetas de credito",
                      "credito personal", "creditos personales",
                      "banca de consumo", "credito revolvente"),
    "captacion_ahorro": ("captacion", "cuenta de ahorro", "cuentas de ahorro",
                         "deposito a plazo", "depositos a plazo",
                         "pagare bancario", "ahorro voluntario"),
    "credito_automotriz": ("credito automotriz", "creditos automotrices",
                           "financiamiento automotriz", "credito para auto"),
    "pagos_digitales": ("pagos digitales", "pago digital",
                        "terminal punto de venta", "terminales punto de venta",
                        "tpv", "transferencia electronica",
                        "transferencias electronicas", "codi", "dimo", "spei"),
    "insurtech": ("seguros", "aseguradora", "aseguradoras", "poliza", "polizas",
                  "insurtech"),
    "banca": ("banca multiple", "sistema financiero", "institucion de banca",
              "instituciones de banca", "cartera vencida",
              "indice de morosidad", "margen financiero"),
    "bursatil": ("bolsa mexicana de valores", "bmv", "biva", "indice de precios",
                 "s&p/bmv ipc", "emisora", "emisoras", "oferta publica"),
}
