"""Emisoras con eventos relevantes vía el portal de divulgación de la BMV
(reemplazo de `bmv_eventos`, 25-ago-2026).

`bmv_eventos` nunca funcionó: la página `Eventos_relevantes` es una SPA sin
tabla en el HTML crudo (riesgo nº3 del PRD §9, confirmado ADR-11) y
`docs-pub/eventemi/` devuelve 403 al listar el directorio directamente. La
alternativa que sí funciona: cada emisora tiene una página PROPIA de eventos
relevantes en el mismo portal que ya usa `reportes_ir`
(`bmv.com.mx/es/emisoras/eventosrelevantes/{clave}-{id}-CGEN_CAPIT`), y esa sí
es HTML estático con datos reales — verificado en vivo el 25-ago-2026 contra
Inbursa: filas con fecha, asunto ("Adquisición de Entidad Financiera",
"Fitch Afirma Calificación...") y enlace al documento.

La "clave" es decorativa, igual que en `reportes_ir` (mismo portal, misma
regla) — solo el ID numérico importa. Se reutilizan los IDs ya resueltos a
mano para `reportes_ir`: BBAJIOO, GENTERA, GFINBURO, Q, RA. Banorte y BOLSAA
quedan fuera de esta lista porque su entrada en `reportes_ir` usa su propio
sitio o el portal de reportes financieros de la BMV, no este — no se
resolvió su ID numérico en ESTE portal. Ampliar a ellas o al resto del
universo (Walmex, AMX, CEMEX...) requiere la misma investigación manual que
ya se hizo una vez para las cinco de aquí abajo — pendiente, no bloqueante.
"""

from __future__ import annotations

from typing import NamedTuple


class EmisoraEventos(NamedTuple):
    ticker: str
    id_bmv: int
    nombre: str


EMISORAS_EVENTOS: tuple[EmisoraEventos, ...] = (
    EmisoraEventos("BBAJIOO.MX", 31431, "Banco del Bajío"),
    EmisoraEventos("GENTERA.MX", 7472, "Gentera (Compartamos Banco)"),
    EmisoraEventos("GFINBURO.MX", 5428, "Grupo Financiero Inbursa"),
    EmisoraEventos("Q.MX", 7790, "Quálitas Controladora"),
    EmisoraEventos("RA.MX", 6808, "Regional / Banregio"),
)
