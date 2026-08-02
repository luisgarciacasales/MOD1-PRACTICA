"""Contratos de datos Pydantic de la capa Silver.

STUB DEL SCAFFOLD — los modelos (SilverNews, MarketPrice, MacroIndicator) y las
convenciones de silver_dead_letters los define el skill `data-contracts`:
    .claude/skills/data-contracts/SKILL.md

Reglas que el contrato debe hacer cumplir (PRD §6.2):
  · tipado estricto: tipos, longitudes, formato de fecha, URL válida
  · integridad semántica: al menos un Ticker, Sector o Entidad
  · bypass macroeconómico: macro_bypass = true deja pasar noticias sin ticker
  · rechazo -> silver_dead_letters con motivo (MISSING_ENTITY, INVALID_URL, ...)
"""
