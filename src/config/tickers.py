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
# Nota sobre el PRD §9: recomienda "lista de tickers acotada (8-12)" como
# mitigación del rate limiting de Yahoo. Con 13 se excede por poco; la pausa de
# 0,6 s entre solicitudes de `src/sources/market.py` sigue siendo la defensa.
TICKERS_PRIORITARIOS: tuple[str, ...] = (
    # --- Banca y servicios financieros ---
    "GFNORTEO.MX",   # Grupo Financiero Banorte
    "BBAJIOO.MX",    # Banco del Bajío
    "GENTERA.MX",    # Gentera (Compartamos) — microcrédito al consumo
    "RA.MX",         # Regional / Banregio — matriz de Hey Banco
    "GFINBURO.MX",   # Grupo Financiero Inbursa
    "BOLSAA.MX",     # Bolsa Mexicana de Valores (la operadora, no el índice)
    "Q.MX",          # Quálitas Controladora — aseguradora
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
    # Redes de TPV y banca digital. Inbursa opera adquirencia y corresponsales.
    "pagos_digitales": ("GFNORTEO.MX", "GFINBURO.MX"),
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
