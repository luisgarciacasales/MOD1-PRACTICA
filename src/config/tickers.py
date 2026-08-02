"""Universo de tickers y mapeo sector→proxy (PRD §3.3 y §3.4).

Este módulo es **configuración declarativa**, no lógica: la inferencia del
sector la hace el LLM en la etapa de enriquecimiento (skill medallion-pipeline);
aquí solo vive la tabla que traduce ese sector a un ticker que sí cotiza.
"""

# Emisoras prioritarias de Fase 1 (PRD §3.4). El sufijo .MX es el que Yahoo
# Finance usa para la Bolsa Mexicana de Valores.
TICKERS_PRIORITARIOS: tuple[str, ...] = (
    "GFNORTE.MX",
    "BBAJIO.MX",
    "WALMEX.MX",
    "AMXL.MX",
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
SECTOR_A_PROXY: dict[str, tuple[str, ...]] = {
    # Mayor exposición a crédito al consumo y tarjetas.
    "banca_consumo": ("GFNORTE.MX", "BBAJIO.MX"),
    # Líder en captación tradicional en México.
    "captacion_ahorro": ("GFNORTE.MX",),
    # Fuerte presencia en financiamiento automotriz.
    "credito_automotriz": ("BBAJIO.MX",),
    # Propietario de redes de TPV y banca digital.
    "pagos_digitales": ("GFNORTE.MX",),
    # Proxy reconocidamente débil: WALMEX no es una aseguradora. El PRD lo marca
    # para revisión en Fase 2, cuando se busque una aseguradora listada (GNP).
    "insurtech": ("WALMEX.MX",),
}

# Sectores válidos que el LLM puede devolver. Restringir el vocabulario evita
# que la inferencia invente etiquetas que luego no mapean a ningún proxy.
SECTORES_VALIDOS: frozenset[str] = frozenset(SECTOR_A_PROXY)
