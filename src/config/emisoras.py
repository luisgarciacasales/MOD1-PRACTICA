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
    # Matrices españolas vía SIC (ver EMISORAS_SIC en config/tickers.py). Los
    # alias apuntan a la FILIAL mexicana porque es de ella de la que habla la
    # prensa; el precio disponible es el de la matriz, y esa asimetría es
    # justamente la salvedad documentada.
    # "bancomer" se incluye porque BBVA México operó como BBVA Bancomer y el
    # nombre sigue muy vivo en el lenguaje periodístico.
    "BBVA.MX": ("bbva", "bbva mexico", "bbva bancomer", "bancomer",
                "banco bilbao vizcaya"),
    # OJO: "santander" a secas sí se acepta —en prensa mexicana casi siempre
    # designa al banco— pero es un apellido y un topónimo (la ciudad española).
    # Es un riesgo asumido y acotado: el corpus es financiero.
    "SANN.MX": ("santander", "banco santander", "santander mexico"),
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
# `banregio`, `compartamos`, `bbva mexico` y `santander mexico`: ahora todas
# tienen un ticker asociado (GFINBURO.MX, RA.MX, GENTERA.MX, BBVA.MX, SANN.MX) y
# tenerlas en los dos sitios las habría etiquetado a la vez como emisora y como
# entidad sin cotización, que es contradictorio.
ENTIDADES_FINANCIERAS: tuple[str, ...] = (
    "banxico", "banco de mexico", "citibanamex",
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
                      "banca de consumo", "credito revolvente",
                      "credito nomina", "buy now pay later", "compra ahora paga despues"),
    # "captacion" a secas se RETIRÓ el 2026-08-03: una nota titulada "la
    # captación pluvial como pilar de infraestructura" acabó clasificada como
    # captación bancaria. Ahora se exige el término compuesto.
    "captacion_ahorro": ("captacion bancaria", "captacion tradicional",
                         "captacion de recursos", "captacion de depositos",
                         "cuenta de ahorro", "cuentas de ahorro",
                         "deposito a plazo", "depositos a plazo",
                         "pagare bancario", "ahorro voluntario"),
    "credito_automotriz": ("credito automotriz", "creditos automotrices",
                           "financiamiento automotriz", "credito para auto"),
    "pagos_digitales": ("pagos digitales", "pago digital",
                        "terminal punto de venta", "terminales punto de venta",
                        "tpv", "transferencia electronica",
                        "transferencias electronicas", "codi", "dimo", "spei"),
    # NUEVO (2026-08-03) — es la frontera real de competencia entre banca y
    # fintechs, y por eso sí resuelve proxy ticker. Va ANTES de "banca" para que
    # una nota sobre digitalización bancaria no caiga en el sector genérico.
    #
    # TODOS los términos exigen contexto bancario explícito. En la primera
    # versión incluí "digitalizacion" y "transformacion digital" a secas, y
    # etiquetaron como banca digital una nota sobre la ley de inteligencia
    # artificial, otra sobre "green skills" y una tercera sobre una fusión de
    # despachos legales. Son muletillas de negocio, no vocabulario financiero.
    # Igual se descartó "experiencia del cliente", que vale para cualquier
    # sector, y "onboarding digital", que se usa también en recursos humanos.
    "banca_digital": ("banca digital", "banco digital", "banca movil",
                      "banca en linea", "banca por internet",
                      "digitalizacion bancaria", "digitalizacion financiera",
                      "digitalizacion de la banca",
                      "hiperpersonalizacion", "hiper personalizacion",
                      "sucursal digital", "app bancaria", "aplicacion bancaria",
                      "neobanco", "neobancos", "banco 100% digital"),
    "insurtech": ("seguros", "aseguradora", "aseguradoras", "poliza", "polizas",
                  "insurtech", "primaje", "siniestralidad"),
    # Sector genérico: temas de banca que NO son un segmento de producto
    # concreto. No resuelve proxy —una nota sobre fraude o regulación no habla
    # de una fintech compitiendo— pero sí hace la noticia identificable y por
    # tanto la salva de la cuarentena.
    "banca": ("banca multiple", "sistema financiero", "institucion de banca",
              "instituciones de banca",
              # Riesgo de crédito
              "cartera vencida", "indice de morosidad", "tasa de morosidad",
              "tasas de morosidad", "morosidad", "provisiones preventivas",
              "quebranto",
              # Rentabilidad y fondeo
              "margen financiero", "costo de fondeo", "costos de fondeo",
              "margen de interes", "spread bancario",
              # Liquidez y solvencia
              "liquidez bancaria", "liquidez del sistema", "coeficiente de cobertura",
              "indice de capitalizacion", "requerimiento de capital", "basilea",
              # Fraude y seguridad
              "fraude bancario", "fraudes bancarios", "robo de identidad",
              "suplantacion de identidad", "lavado de dinero",
              "prevencion de lavado", "ciberseguridad bancaria",
              # Regulación y supervisión
              "regulacion bancaria", "regulacion financiera", "marco regulatorio",
              "ley fintech", "cnbv", "condusef", "sanciones regulatorias",
              "comisiones bancarias"),
    "bursatil": ("bolsa mexicana de valores", "bmv", "biva", "indice de precios",
                 "s&p/bmv ipc", "emisora", "emisoras", "oferta publica",
                 "colocacion de deuda", "oferta publica inicial"),
}
