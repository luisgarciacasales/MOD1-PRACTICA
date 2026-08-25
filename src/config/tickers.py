"""Universo de tickers y mapeo sector→proxy (PRD §3.3 y §3.4).

Este módulo es **configuración declarativa**, no lógica: la inferencia del
sector la hace el LLM en la etapa de enriquecimiento (skill medallion-pipeline);
aquí solo vive la tabla que traduce ese sector a un ticker que sí cotiza.
"""

# Emisoras prioritarias de Fase 1 (PRD §3.4). El sufijo .MX es el que Yahoo
# Finance usa para la Bolsa Mexicana de Valores.
#
# CORRECCIÓN RESPECTO AL PRD (verificado contra Yahoo el 2026-08-01, ADR-11):
# tres de los ocho símbolos del documento no existen en Yahoo y devuelven serie
# vacía. Yahoo exige la **serie accionaria** en el símbolo:
#   GFNORTE.MX → GFNORTEO.MX   (Banorte cotiza la serie O)
#   BBAJIO.MX  → BBAJIOO.MX    (Banco del Bajío, serie O)
#   AMXL.MX    → AMXB.MX       (América Móvil convirtió las series L a B)
# Los otros cinco son correctos tal cual.
# AMPLIACIÓN (2026-08-03): se añaden cinco emisoras del sector financiero, todas
# verificadas contra Yahoo antes de incorporarlas. Motivo: el objetivo de negocio
# del PRD §1 es la competitividad de la banca tradicional frente a las fintechs,
# y con solo dos bancos en el universo (Banorte y BanBajío) el análisis se
# quedaba corto. Se eligió profundizar en el sector en lugar de ampliar a todo el
# IPC, para no diluir el foco.
#
# AMPLIACIÓN (2026-08-25, roadmap F1): se evaluaron cinco candidatos más del
# sector para el mismo objetivo — profundizar financiero, no ampliar a todo el
# IPC. Verificado contra Yahoo el mismo día:
#
#   ACTINVRB.MX (Actinver, casa de bolsa)     → OK, se incorpora (ver abajo)
#   FINDEP.MX   (Financiera Independencia)    → datos existen pero 38/66
#                                                sesiones sin operar (58%) en
#                                                los últimos 3 meses — ticker
#                                                fantasma, descartado
#   VALUEGFO.MX (Value Grupo Financiero)      → volumen 0 en 66/66 sesiones —
#                                                no opera en la práctica,
#                                                descartado
#   UNIFINA.MX  (Unifin Financiera)           → sin datos en Yahoo, consistente
#                                                con su default de 2023
#   CREAL.MX    (Crédito Real)                → sin datos en Yahoo, consistente
#                                                con su default/concurso
#
# Conclusión: el universo financiero líquido de BMV está prácticamente
# saturado. No repetir esta búsqueda sin evidencia nueva de que alguno de los
# cuatro descartados volvió a cotizar con volumen real.
#
# Nota sobre el PRD §9: recomienda "lista de tickers acotada (8-12)" como
# mitigación del rate limiting de Yahoo. Ya se excedía con 13; con 14 la pausa
# de 0,6 s entre solicitudes de `src/sources/market.py` sigue siendo la defensa.
TICKERS_PRIORITARIOS: tuple[str, ...] = (
    # --- Banca y servicios financieros ---
    "GFNORTEO.MX",   # Grupo Financiero Banorte
    "BBAJIOO.MX",    # Banco del Bajío
    "GENTERA.MX",    # Gentera (Compartamos) — microcrédito al consumo
    "RA.MX",         # Regional / Banregio — matriz de Hey Banco
    "GFINBURO.MX",   # Grupo Financiero Inbursa
    "BOLSAA.MX",     # Bolsa Mexicana de Valores (la operadora, no el índice)
    "Q.MX",          # Quálitas Controladora — aseguradora
    "ACTINVRB.MX",   # Actinver — casa de bolsa / gestión de activos (2026-08-25)
    # --- Matrices extranjeras vía SIC (leer la advertencia de abajo) ---
    "BBVA.MX",       # Banco Bilbao Vizcaya Argentaria S.A. (matriz de BBVA México)
    "SANN.MX",       # Banco Santander S.A. (matriz de Santander México)
    # --- Resto del universo original ---
    "WALMEX.MX",
    "AMXB.MX",
    "GMEXICOB.MX",
    "CEMEXCPO.MX",
    "FEMSAUBD.MX",
    "ALSEA.MX",
)

# --- Benchmark del mercado ---------------------------------------------------
#
# Índice S&P/BMV IPC. Verificado el 2026-08-04: 501 sesiones en dos años, sin
# volúmenes en cero ni NaN, OHLC coherente y calendario alineado con las
# emisoras.
#
# Por qué NO está en TICKERS_PRIORITARIOS: un índice **no es una emisora**.
# Mantenerlo aparte tiene tres consecuencias deliberadas:
#   · no entra al vocabulario del NER, así que el LLM no puede etiquetar una
#     noticia con el índice como si fuera una empresa;
#   · no aparece en ALIAS_EMISORAS, así que la extracción léxica no lo detecta
#     —y conviene, porque "la BMV cerró al alza" habla del índice y no de una
#     compañía en la que se pueda invertir el análisis de impacto;
#   · no genera filas en gold_news_market_corr, porque correlacionar una
#     noticia con el mercado entero no dice nada sobre ninguna institución.
#
# Su única función es servir de referencia: sin él no hay beta, ni rendimiento
# relativo, ni exceso de retorno, que son las métricas que distinguen "subió
# 3%" de "superó al mercado en 1,8%".
BENCHMARK: str = "^MXX"

# Lo que realmente se descarga de Yahoo: las emisoras más el benchmark.
TICKERS_MERCADO: tuple[str, ...] = (*TICKERS_PRIORITARIOS, BENCHMARK)

# Emisoras cuyo precio hay que interpretar con cuidado. Se ingieren igual, pero
# cualquier análisis que las use debe declarar la salvedad.
#
# BBVA.MX y SANN.MX cotizan en la BMV a través del **SIC** (Sistema Internacional
# de Cotizaciones) y NO son las filiales mexicanas: son las matrices españolas
# (Yahoo las reporta con `country: Spain`). Dos consecuencias:
#
#   1. El precio es un ESPEJO. Lo fija la plaza primaria de Madrid y la
#      conversión a pesos, así que `daily_return_pct` mezcla el movimiento de la
#      acción con el del tipo de cambio EUR/MXN. Un retorno puede ser cambiario
#      y no una reacción a la noticia.
#   2. La exposición a México está DILUIDA. México pesa mucho en el resultado de
#      BBVA y bastante menos en el de Santander; el resto responde a España,
#      Turquía y Sudamérica.
#
# Se incluyen de todos modos porque BBVA México y Santander México son el primero
# y el tercer banco del país por activos y aparecen constantemente en la prensa:
# sin ellas, ninguna noticia sobre esos bancos llegaba nunca a correlacionarse.
# Es el mismo razonamiento del proxy ticker del PRD §3.3 — un proxy imperfecto y
# declarado vale más que ningún precio.
#
# Su liquidez además es baja (~14 000 títulos de media frente a 6,6 M de
# Banorte), y SANN.MX tiene un 12% de sesiones sin operar.
EMISORAS_SIC: frozenset[str] = frozenset({"BBVA.MX", "SANN.MX"})

# Ventana histórica inicial; después la ingesta es incremental diaria (PRD §3.4).
VENTANA_HISTORICA_ANIOS: int = 2


# --- Proxy ticker para fintechs sin cotización (PRD §3.3) --------------------
#
# El problema: Nu, Ualá, Stori y Klar no cotizan en la BMV, así que una noticia
# sobre ellas no puede hacer JOIN con gold_market_prices. La solución del PRD es
# medir el impacto sobre la emisora listada más expuesta a ese sector.
#
# Cuando se usa esta tabla, el registro resultante lleva is_proxy = true y
# conserva la fintech original en `original_fintech` para no perder trazabilidad.
# Los símbolos llevan la serie accionaria por la misma razón que arriba: deben
# poder hacer JOIN con lo que realmente se ingirió de Yahoo.
SECTOR_A_PROXY: dict[str, tuple[str, ...]] = {
    # Mayor exposición a crédito al consumo y tarjetas. Gentera (Compartamos)
    # entra porque su negocio ES el microcrédito al consumo, que es justo el
    # segmento donde compiten Kueski, Nelo y Aplazo.
    "banca_consumo": ("GFNORTEO.MX", "BBAJIOO.MX", "GENTERA.MX"),
    # Captación tradicional. Banregio se suma por una razón concreta: es la
    # matriz de Hey Banco, así que la competencia de los neobancos por el
    # depósito retail la afecta de forma directa, no analógica.
    "captacion_ahorro": ("GFNORTEO.MX", "RA.MX"),
    # Fuerte presencia en financiamiento automotriz.
    "credito_automotriz": ("BBAJIOO.MX",),
    # Redes de TPV y adquirencia. Inbursa opera adquirencia y corresponsales.
    "pagos_digitales": ("GFNORTEO.MX", "GFINBURO.MX"),
    # NUEVO (2026-08-03) — la digitalización y la hiperpersonalización son la
    # frontera donde los neobancos compiten con la banca. Banregio va primero
    # por ser la matriz de Hey Banco, un banco digital de pleno derecho;
    # Banorte por el tamaño de su apuesta digital.
    "banca_digital": ("RA.MX", "GFNORTEO.MX"),
    # CORREGIDO (2026-08-03): antes apuntaba a WALMEX, que no es una aseguradora
    # — debilidad que el propio PRD §3.3 marcaba para revisión en Fase 2.
    # Quálitas sí cotiza y sí es aseguradora: el proxy pasa a ser real en vez
    # de analógico.
    "insurtech": ("Q.MX",),
}

# NOTA — las emisoras de EMISORAS_SIC (BBVA.MX, SANN.MX) se dejan FUERA de este
# mapeo a propósito, aunque BBVA México y Santander México sean competidores
# directos de los neobancos. Motivo: ya son proxies imperfectos de sus filiales,
# y usarlas además como proxy sectorial de una fintech apilaría dos
# aproximaciones —fintech → sector → matriz extranjera— cuyo error compuesto
# haría el resultado indefendible. Sí se correlacionan de forma DIRECTA cuando
# la noticia las menciona, que es donde su señal es interpretable.

# Sectores válidos que el LLM puede devolver. Restringir el vocabulario evita
# que la inferencia invente etiquetas que luego no mapean a ningún proxy.
SECTORES_VALIDOS: frozenset[str] = frozenset(SECTOR_A_PROXY)


# --- Emisoras con moneda de reporte distinta a la de cotización (F2) --------
#
# Descubierto al desplegar el motor de valuación (25-ago-2026): el P/U de
# CEMEXCPO.MX salía en ~448x y el de GMEXICOB.MX en ~273x — números sin
# sentido económico. Causa verificada vía yfinance (`financialCurrency` vs.
# `currency`): ambas reportan sus estados financieros en USD mientras el
# precio cotiza en MXN en la BMV. Dividir un precio en pesos entre una UPA en
# dólares no es un P/U, es un artefacto de conversión.
#
# BBVA.MX y SANN.MX ya cargaban una salvedad por otra razón (EMISORAS_SIC:
# son la matriz extranjera, no la filial mexicana) y coinciden en el mismo
# efecto práctico — reportan en EUR.
#
# _SQL_VALUATION (transform.py) excluye estas cuatro de P/U hasta que exista
# conversión FX. SF43718 (USD/MXN, ya se ingiere vía Banxico) resolvería
# CEMEX/GMéxico; BBVA/SANN necesitarían EUR/MXN, que hoy no está en el
# universo de series de Banxico — ver src/config/banxico_series.py.
TICKERS_MONEDA_FINANCIERA_DISTINTA: frozenset[str] = frozenset({
    "CEMEXCPO.MX", "GMEXICOB.MX", "BBVA.MX", "SANN.MX",
})
