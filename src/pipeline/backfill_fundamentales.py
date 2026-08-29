"""Backfill de fundamentales trimestrales desde los PDF de resultados.

Yahoo tope en ~4 años de serie anual y 7 trimestres, lo que dejaba la valuación
de cada emisora en unos 886 días frente a 2.513 de precio. Los reportes
trimestrales en PDF llegan mucho más atrás y son la **fuente primaria** — el
documento que la propia emisora publica.

    docker compose exec -T app python -m src.pipeline.backfill_fundamentales \\
        --dir /app/data/manual_dropzone/banorte_historico --ticker GFNORTEO.MX
    ... --dry-run     # extrae y muestra sin escribir

Piloto sobre Banorte (28-ago-2026): 29 PDF ya descargados a mano cubriendo
2018-Q1 a 2025-Q1. Escalar al resto de emisoras exigiría ~460 PDF con el mismo
procedimiento manual, así que esto es una prueba de concepto sobre el caso de
estudio del proyecto, no un mecanismo general.

**Por qué el PDF gana a Yahoo cuando ambos existen.** Validación cruzada entre
los propios PDF: cada reporte incluye el mismo trimestre del año anterior, y en
24 de 24 pares comparables el valor coincide **exactamente** con el que declara
el PDF de ese trimestre. Frente a eso, Yahoo devuelve la UPA del primer
trimestre **redondeada a entero** en cuatro emisoras financieras (GFNORTEO 5.0
donde el reporte dice 5.435, y lo mismo en BBAJIOO, GENTERA y GFINBURO en 2025
y 2026). Así que en el solapamiento el PDF sobrescribe, y no al revés.

**Formato de las cifras.** Cada fila del reporte trae tres valores —mismo
trimestre del año anterior, trimestre anterior, y actual— seguidos de dos
variaciones porcentuales. El que interesa es **el tercero**. Se comprobó contra
las variaciones que el propio PDF publica: en 1T25, 5.435/4.878−1 = 11% y
5.435/4.927−1 = 10%, que son exactamente los dos porcentajes impresos.
"""

from __future__ import annotations

import argparse
import io
import re
import sys
from datetime import date
from pathlib import Path

from src.contracts import validar_fundamental
from src.pipeline import db
from uuid import uuid4

# 1T→31 de marzo, y así. El nombre del archivo es la única fuente de la fecha:
# el texto del PDF trae el corte del periodo en varias formas y ninguna es
# fiable de parsear (mismo problema que motivó `fecha_aproximada_de_trimestre`
# en reportes_ir.py).
FIN_DE_TRIMESTRE = {"1": (3, 31), "2": (6, 30), "3": (9, 30), "4": (12, 31)}

# Las cifras de balance vienen en MILLONES de pesos en el reporte; el contrato
# las guarda en pesos, igual que las de Yahoo.
MILLONES = 1_000_000

CAMPOS = {
    # nombre del contrato → (patrón de la etiqueta, multiplicador)
    "utilidad_por_accion": (r"Utilidad por Acci[oó]n\s*\(Pesos\)(?:\s*\(\d\))?", 1),
    "capital_contable": (r"Capital Contable", MILLONES),
    "utilidad_neta": (r"Utilidad Neta\s*(?:\(1\))?\s*(?=[\d,])", MILLONES),
}


def _texto(pdf: bytes) -> str:
    from pypdf import PdfReader

    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)


def _valor_actual(texto: str, patron: str) -> float | None:
    """Tercer número de la fila: el del trimestre que reporta el documento."""
    m = re.search(patron + r"[^\n\d]{0,40}((?:[\d,]+\.?\d*\s+){2,4})", texto, re.I)
    if not m:
        return None
    numeros = [float(x.replace(",", "")) for x in m.group(1).split()]
    return numeros[2] if len(numeros) >= 3 else None


def _periodo(nombre: str) -> date | None:
    """`1T18.pdf` → 2018-03-31. Devuelve None si el nombre no encaja."""
    m = re.fullmatch(r"([1-4])T(\d{2})", nombre)
    if not m:
        return None
    trimestre, anio = m.groups()
    mes, dia = FIN_DE_TRIMESTRE[trimestre]
    return date(2000 + int(anio), mes, dia)


def extraer(directorio: Path, ticker: str) -> tuple[list, list[str]]:
    """Devuelve (filas válidas, incidencias). No escribe nada."""
    filas, incidencias = [], []
    batch = uuid4()

    for archivo in sorted(directorio.glob("*.pdf")):
        periodo = _periodo(archivo.stem)
        if periodo is None:
            incidencias.append(f"{archivo.name}: nombre fuera del patrón {{n}}T{{aa}}")
            continue

        texto = _texto(archivo.read_bytes())
        crudo = {"ticker": ticker, "period_end": periodo}
        for campo, (patron, factor) in CAMPOS.items():
            valor = _valor_actual(texto, patron)
            if valor is not None:
                crudo[campo] = valor * factor

        # Sin UPA no hay P/U, que es la razón de ser del backfill. Se registra
        # como incidencia en vez de cargar una fila que no sirve para nada.
        if crudo.get("utilidad_por_accion") is None:
            incidencias.append(f"{archivo.name}: sin UPA extraíble")
            continue

        resultado = validar_fundamental(crudo, batch)
        if hasattr(resultado, "rejection_reason"):
            incidencias.append(f"{archivo.name}: rechazado — {resultado.rejection_detail}")
        else:
            filas.append(resultado)

    return filas, incidencias


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backfill_fundamentales",
        description="Carga fundamentales trimestrales desde PDF de resultados.",
    )
    parser.add_argument("--dir", required=True, help="Directorio con los PDF ({n}T{aa}.pdf).")
    parser.add_argument("--ticker", required=True, help="Emisora a la que pertenecen.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Extrae y muestra sin escribir en Silver.")
    args = parser.parse_args(argv)

    directorio = Path(args.dir)
    if not directorio.is_dir():
        print(f"[backfill] {directorio} no es un directorio", file=sys.stderr)
        return 1

    filas, incidencias = extraer(directorio, args.ticker)
    if not filas:
        print(f"[backfill] ningún PDF utilizable en {directorio}", file=sys.stderr)
        for i in incidencias:
            print(f"  {i}", file=sys.stderr)
        return 1

    print(f"[backfill] {args.ticker} · {len(filas)} trimestres extraídos "
          f"({min(f.period_end for f in filas)} → {max(f.period_end for f in filas)})")
    for i in incidencias:
        print(f"[backfill] incidencia: {i}")

    if args.dry_run:
        print(f"\n{'PERIODO':<13}{'UPA':>9}{'CAPITAL (mdp)':>16}")
        for f in sorted(filas, key=lambda x: x.period_end):
            cap = f.capital_contable / MILLONES if f.capital_contable else 0
            print(f"{str(f.period_end):<13}{f.utilidad_por_accion:>9.3f}{cap:>16,.0f}")
        print("\n[backfill] DRY RUN — no se escribió nada.")
        return 0

    with db.conectar() as conexion, conexion.cursor() as cur:
        carga = db.cargar_fundamentales(cur, filas)
        conexion.commit()

    print(f"[backfill] {carga.nuevas} nuevas · {carga.actualizadas} actualizadas")
    if carga.actualizadas:
        print("[backfill] las actualizadas sobrescriben datos de Yahoo en el "
              "solapamiento, que es lo pretendido: el PDF es la fuente primaria "
              "y Yahoo redondea la UPA del primer trimestre.")
    print("[backfill] corre `transform` para recalcular gold_valuation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
