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
    # --- Resto del universo original ---
    "WALMEX.MX",
    "AMXB.MX",
    "GMEXICOB.MX",
    "CEMEXCPO.MX",
    "FEMSAUBD.MX",
    "ALSEA.MX",
)

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

# Sectores válidos que el LLM puede devolver. Restringir el vocabulario evita
# que la inferencia invente etiquetas que luego no mapean a ningún proxy.
SECTORES_VALIDOS: frozenset[str] = frozenset(SECTOR_A_PROXY)
