"""Adaptadores de las 5 fuentes de datos (PRD §3).

STUB DEL SCAFFOLD — la implementación la guía el skill `medallion-pipeline`.

Contrato común de cada adaptador: devolver el payload TAL CUAL lo entregó el
origen, sin limpieza. La inmutabilidad de Bronze (PRD §6.1) depende de que
estos módulos no transformen nada.

Cada uno debe ser fail-soft: una excepción aquí no puede tumbar el batch de las
otras fuentes.
"""
