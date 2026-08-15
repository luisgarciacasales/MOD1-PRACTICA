"""Emisoras piloto del reporte narrativo trimestral (ampliación 15-ago-2026).

Investigación en vivo del 15-ago-2026 (`curl` crudo, sin JS, más extracción
real con `pypdf`): las tres publican el comunicado de resultados **solo en
PDF**, nunca en HTML navegable. Donde sí varía es la página LISTADO que
enlaza al PDF — de eso depende si se puede raspar sin navegador headless:

- **Banorte** — su propio sitio (`investors.banorte.com`) es HTML
  servidor-renderizado: se usa directamente.
- **Regional / Banregio** — su sitio propio (`regional.mx`) es una SPA
  Angular que no renderiza sin JS (confirmado: el HTML crudo trae
  placeholders de plantilla sin resolver, tipo `{{::e.esp}}`). Se usa en su
  lugar el portal de divulgación de la propia BMV
  (`bmv.com.mx/.../informacionfinanciera/{id}`), que resultó ser HTML
  estático y publica el mismo PDF narrativo. Es potencialmente generalizable
  a cualquier emisora que cotice en BMV, no solo Regional — motivo por el que
  se prioriza sobre construir un scraper por sitio corporativo.
- **Bolsa Mexicana de Valores** — su sitio ES bmv.com.mx, sin ambigüedad.

Piloto de 3 antes de mapear las 15 (decisión explícita del usuario,
15-ago-2026): confirma que el extractor de PDF generaliza entre sitios antes
de invertir el trabajo manual de mapear las 12 emisoras restantes.

AMPLIACIÓN 15-ago-2026 — mapeo de las 12 restantes contra el portal de la BMV.
Verificado con `curl` crudo (dos hallazgos que cambiaron el diseño):

1. **La "clave" de la URL es decorativa.** Solo el ID numérico determina qué
   emisora se carga — confirmado pidiendo el mismo ID con una clave inventada
   y comparando el HTML byte a byte (idéntico). Se conserva una clave legible
   en las URLs de abajo por claridad, no porque el servidor la necesite.
2. **El prefijo del PDF narrativo depende de la categoría regulatoria de la
   emisora, no es fijo `sominfin_`.** Solo 4 de las 12 restantes tienen
   narrativo en este portal — ver `src/sources/reportes_ir.py` para el
   detalle de las categorías (`bnc`/`gps`/`asg`/`som`).

Resultado: se suman **BBAJIOO, GENTERA, GFINBURO y Q** (bancos, grupo
financiero y aseguradora — sí tienen narrativo). Quedan **fuera** del piloto,
verificado y no por omisión:

- **Walmex, AMX, Grupo México, CEMEX, FEMSA, Alsea** — corporativos no
  financieros; este portal solo trae XBRL para ellos, sin PDF narrativo.
- **BBVA.MX y SANN.MX** — matrices españolas vía SIC (ver `EMISORAS_SIC` en
  `src/config/tickers.py`); su última divulgación periódica en este portal es
  un 10-K/8-K anual, no un narrativo trimestral mexicano. Consistente con la
  exposición diluida a México ya documentada para estas dos.

Para esas 8, el pipeline simplemente no las incluye — no hay una fuente
narrativa equivalente identificada todavía. Una vía no explorada: el listado
"Eventos Relevantes" de la propia BMV (`docs-pub/eventemi/`), que sí mostró
contenido para CEMEX/AMX/Alsea en una búsqueda exploratoria — pendiente de
investigación si se decide perseguirlo.
"""

from __future__ import annotations

from typing import NamedTuple


class EmisoraIR(NamedTuple):
    ticker: str
    listado_url: str
    nombre: str


EMISORAS_IR: tuple[EmisoraIR, ...] = (
    EmisoraIR(
        "GFNORTEO.MX",
        "https://investors.banorte.com/es/financial-information/quarterly-reports",
        "Grupo Financiero Banorte",
    ),
    EmisoraIR(
        "RA.MX",
        # ID interno de BMV para Regional/Banregio (la "clave" en la URL es
        # decorativa, ver docstring del módulo) — resuelto a mano el
        # 15-ago-2026 contra el portal de divulgación de la BMV.
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/START-6808",
        "Regional / Banregio",
    ),
    EmisoraIR(
        "BOLSAA.MX",
        "https://www.bmv.com.mx/es/relacion-con-inversionistas/reportes-financieros",
        "Bolsa Mexicana de Valores",
    ),
    # --- Ampliación 15-ago-2026: las 4 de las 12 restantes que sí tienen
    # narrativo en el portal de la BMV (ver docstring del módulo). IDs
    # verificados con curl crudo.
    EmisoraIR(
        "BBAJIOO.MX",
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/BBAJIO-31431",
        "Banco del Bajío",
    ),
    EmisoraIR(
        "GENTERA.MX",
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/GENTERA-7472",
        "Gentera (Compartamos Banco)",
    ),
    EmisoraIR(
        "GFINBURO.MX",
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/GFINBUR-5428",
        "Grupo Financiero Inbursa",
    ),
    EmisoraIR(
        "Q.MX",
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/Q-7790",
        "Quálitas Controladora",
    ),
)
