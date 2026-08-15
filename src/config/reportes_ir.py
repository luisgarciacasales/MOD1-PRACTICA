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
        # ID interno de BMV para Regional/Banregio (ticker de emisora "START"),
        # NO el ticker de Yahoo — resuelto a mano el 15-ago-2026 contra el
        # portal de divulgación de la BMV.
        "https://www.bmv.com.mx/es/emisoras/informacionfinanciera/START-6808",
        "Regional / Banregio",
    ),
    EmisoraIR(
        "BOLSAA.MX",
        "https://www.bmv.com.mx/es/relacion-con-inversionistas/reportes-financieros",
        "Bolsa Mexicana de Valores",
    ),
)
