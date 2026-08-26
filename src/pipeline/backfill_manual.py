"""Backfill manual — reportes trimestrales históricos descargados a mano
(25-ago-2026, roadmap F2/F3: profundidad histórica para Banorte).

`investors.banorte.com/.../quarterly-reports` solo es HTML estático hasta
2025-Q2 hacia atrás desde hoy (ver ADR-16, docs/HARNESS.md); antes de eso el
selector de año es JavaScript puro y no hay endpoint accesible sin navegador
headless — mismo tipo de obstáculo que ya se documentó para `bmv_eventos`.
En vez de invertir en Playwright/Selenium, el usuario descarga los PDF a
mano desde el navegador (donde el JS sí renderiza) y los coloca en una
carpeta local; este script los convierte en un lote Bronze más, reutilizando
la misma extracción de texto que `reportes_ir` (`_desde_el_resumen`) — mismo
contrato (`SilverNews` vía `source="reportes_ir"`), mismo `enrich`, mismo
`correlate`. No es una fuente nueva: es el mismo `reportes_ir` con un
localizador manual en vez de uno que raspa una página.

Convención de nombre de archivo, EXACTA — un solo PDF por trimestre, el
reporte principal (nunca "Reporte Administracion de Riesgos" ni "ANEXO"):

    {n}T{aa}.pdf     ejemplo: 1T18.pdf  (primer trimestre de 2018)

Mismo patrón que usa el propio Banorte en sus URLs automáticas
(`_localizar_banorte` en reportes_ir.py) — no es arbitrario, es reconocible
sin instrucciones adicionales. Un archivo con otro nombre se ignora con
aviso en `fallidos`, no rompe el lote completo (mismo criterio fail-soft que
el resto del pipeline).

Fecha de publicación: **NO** se usa `_fecha_de_texto` (bug encontrado el
25-ago-2026, ver el docstring de `_localizar_banorte` en reportes_ir.py):
en los reportes de Banorte esa función siempre encuentra "Información
Financiera al {DD} de {MES} de {AAAA}" —la fecha de CORTE del periodo, no
la de publicación— porque aparece en la segunda línea del documento, antes
que cualquier dateline real. Usarla habría reintroducido justo el
lookahead bias que se quiso evitar. En su lugar, `fecha_aproximada_de_
trimestre` (reportes_ir.py) calcula `fin_de_trimestre + 45 días` a partir
del propio nombre de archivo — determinista, sin depender de qué texto
aparezca primero en el PDF.

    docker compose exec -T app python -m src.pipeline.backfill_manual \
        --dir /app/data/manual_dropzone/banorte_historico \
        --ticker GFNORTEO.MX --nombre "Grupo Financiero Banorte"
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.pipeline.bronze import escribir_lote
from src.sources.reportes_ir import _desde_el_resumen, fecha_aproximada_de_trimestre

_PATRON_ARCHIVO = re.compile(r"^(\d)T(\d{2})\.pdf$", re.IGNORECASE)


def _procesar_archivo(ruta: Path, nombre: str) -> dict | None:
    m = _PATRON_ARCHIVO.match(ruta.name)
    if not m:
        return None
    trimestre, aa = int(m.group(1)), int(m.group(2))
    anio = 2000 + aa
    fecha = fecha_aproximada_de_trimestre(anio, trimestre)

    # reportes_ir._extraer_texto_pdf espera una URL remota (hace
    # requests.get); para un archivo ya local en disco se lee directo con
    # pypdf, mismo recorte de páginas — ver _texto_de_pdf_local.
    texto_completo = _texto_de_pdf_local(ruta)
    if not texto_completo:
        return None

    # El ticker no se inyecta como campo aparte: igual que en la ingesta en
    # vivo de reportes_ir, el propio título ancla la emisora ("Grupo
    # Financiero Banorte") y la extracción léxica (ALIAS_EMISORAS) ya la
    # reconoce por nombre — mismo mecanismo, un solo camino de extracción.
    return {
        "title": f"Reporte trimestral — {nombre} ({trimestre}T{aa})",
        "summary": _desde_el_resumen(texto_completo),
        "link": f"manual://banorte_historico/{ruta.name}",
        "published": fecha.isoformat(),
        "source": "reportes_ir",
    }


def _texto_de_pdf_local(ruta: Path) -> str:
    from pypdf import PdfReader

    # Mismo recorte que reportes_ir.PAGINAS_A_EXTRAER (12): el resumen
    # ejecutivo no siempre está en la página 1, y el resto son notas
    # contables que ya cubre yahoo_fundamentals de forma estructurada.
    lector = PdfReader(str(ruta))
    paginas = lector.pages[:12]
    return "\n".join(p.extract_text() or "" for p in paginas).strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backfill_manual",
        description="Convierte PDFs trimestrales descargados a mano en un lote Bronze de reportes_ir.",
    )
    parser.add_argument("--dir", required=True, help="Carpeta con los PDF (dentro del contenedor).")
    parser.add_argument("--ticker", required=True, help="Ticker BMV, ej. GFNORTEO.MX")
    parser.add_argument("--nombre", required=True, help="Nombre de la emisora para el título.")
    parser.add_argument("--fecha-lote", default=None, help="Fecha del lote Bronze (YYYY-MM-DD, por defecto hoy).")
    args = parser.parse_args(argv)

    carpeta = Path(args.dir)
    if not carpeta.is_dir():
        print(f"[backfill_manual] no existe la carpeta {carpeta}", file=sys.stderr)
        return 1

    print(f"[backfill_manual] {args.ticker} — {args.nombre} · carpeta {carpeta}")

    registros = []
    fallidos = []
    for ruta in sorted(carpeta.glob("*.pdf")):
        try:
            registro = _procesar_archivo(ruta, args.nombre)
            if registro is None:
                fallidos.append(f"{ruta.name}: nombre no reconocido o PDF sin texto")
                continue
            registros.append(registro)
            print(f"[backfill_manual] {ruta.name}: OK → {registro['published']}")
        except Exception as exc:  # noqa: BLE001
            fallidos.append(f"{ruta.name}: {type(exc).__name__}: {exc}")

    if not registros:
        print(f"[backfill_manual] ningún archivo procesado ({'; '.join(fallidos)})", file=sys.stderr)
        return 1

    fecha_lote = date.fromisoformat(args.fecha_lote) if args.fecha_lote else date.today()
    raiz_bronze = Path(get_settings().bronze_path)
    lote = escribir_lote(
        registros, source="reportes_ir", categoria="news", fecha=fecha_lote, raiz_bronze=raiz_bronze,
    )
    print(f"[backfill_manual] {lote.record_count} registros → {lote.ruta.relative_to(raiz_bronze)}")
    if fallidos:
        print(f"[backfill_manual] {len(fallidos)} archivos con problema: {'; '.join(fallidos)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
