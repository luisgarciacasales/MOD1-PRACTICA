"""Etapa 1 — Ingesta (Origen → Bronze). PRD §4.4 paso 1.

Dispara las 5 fuentes y persiste cada lote en Bronze **sin transformar**.

Dos garantías del skill medallion-pipeline:

· **Fail-soft por fuente.** Una fuente caída no aborta el batch. Se registra su
  error y las demás continúan. El proceso solo devuelve código de error si
  *ninguna* fuente produjo datos, porque un batch sin nada sí es un fracaso.
· **Bronze inmutable.** Cada corrida crea su propio directorio con UUID; nada
  se sobrescribe. Reingerir el mismo día produce un lote nuevo, y la
  deduplicación ocurre después, en Silver, por clave natural.

    docker compose exec -T app python -m src.pipeline.ingest
    docker compose exec -T app python -m src.pipeline.ingest --date 2026-08-01
    docker compose exec -T app python -m src.pipeline.ingest --source financiero
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from src.config import get_settings
from src.pipeline.bronze import escribir_lote
from src.sources import banxico, bmv, finnovista, market, rss
from src.sources.base import ResultadoFuente

# Orden de ejecución: primero lo barato y fiable, al final lo lento (yfinance
# tarda ~5 s por ticker con la pausa antirrate-limit). Así un fallo tardío no
# retrasa el diagnóstico de lo demás.
ADAPTADORES: dict[str, Callable[[], ResultadoFuente]] = {
    "finnovista": finnovista.ingerir,
    "financiero": lambda: rss.ingerir("financiero"),
    "economista": lambda: rss.ingerir("economista"),
    "bloomberg": lambda: rss.ingerir("bloomberg"),
    "bmv_eventos": bmv.ingerir,
    "banxico": banxico.ingerir,
    "yahoo_finance": market.ingerir,
}


def ingerir_todo(
    *,
    fecha: date,
    fuentes: list[str],
    raiz_bronze: Path,
    dry_run: bool = False,
) -> list[tuple[ResultadoFuente, str]]:
    """Ejecuta las fuentes indicadas. Devuelve (resultado, detalle) por fuente."""
    salida: list[tuple[ResultadoFuente, str]] = []

    for nombre in fuentes:
        adaptador = ADAPTADORES[nombre]
        print(f"[ingest] {nombre}: descargando…", flush=True)

        try:
            resultado = adaptador()
        except Exception as exc:  # noqa: BLE001
            # Red de seguridad: aunque los adaptadores ya son fail-soft, un
            # fallo aquí (import roto, config ausente) tampoco puede tumbar el
            # batch de las demás fuentes.
            resultado = ResultadoFuente.fallo(nombre, "news", exc)

        if not resultado.ok:
            print(f"[ingest] {nombre}: FALLO — {resultado.error}", flush=True)
            salida.append((resultado, "—"))
            continue

        if dry_run:
            detalle = f"{len(resultado.registros)} registros (no escritos)"
            print(f"[ingest] {nombre}: OK {detalle}", flush=True)
            salida.append((resultado, detalle))
            continue

        lote = escribir_lote(
            resultado.registros,
            source=resultado.source,
            categoria=resultado.categoria,
            fecha=fecha,
            raiz_bronze=raiz_bronze,
        )
        detalle = f"{lote.record_count} reg → {lote.ruta.relative_to(raiz_bronze)}"
        print(
            f"[ingest] {nombre}: OK {lote.record_count} registros "
            f"(checksum {lote.checksum_sha256[:12]}…)",
            flush=True,
        )
        salida.append((resultado, detalle))

    return salida


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.ingest",
        description="Ingesta batch de las 5 fuentes hacia Bronze (inmutable).",
    )
    parser.add_argument(
        "--date",
        dest="fecha",
        default=None,
        help="Fecha del lote en YYYY-MM-DD (por defecto: hoy en UTC).",
    )
    parser.add_argument(
        "--source",
        dest="fuentes",
        action="append",
        choices=sorted(ADAPTADORES),
        help="Fuente a ingerir; repetible. Por defecto, todas.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Descarga y reporta sin escribir en Bronze.",
    )
    args = parser.parse_args(argv)

    fecha = date.fromisoformat(args.fecha) if args.fecha else datetime.now(UTC).date()
    fuentes = args.fuentes or list(ADAPTADORES)
    raiz_bronze = Path(get_settings().bronze_path)

    print(
        f"[ingest] lote {fecha.isoformat()} · {len(fuentes)} fuentes · destino {raiz_bronze}"
    )
    if args.dry_run:
        print("[ingest] DRY RUN — no se escribirá nada en Bronze")

    resultados = ingerir_todo(
        fecha=fecha, fuentes=fuentes, raiz_bronze=raiz_bronze, dry_run=args.dry_run
    )

    # --- Resumen ------------------------------------------------------------
    print()
    print(f"{'FUENTE':<16} {'ESTADO':<7} DETALLE")
    print("-" * 78)
    ok = 0
    for resultado, detalle in resultados:
        if resultado.ok:
            ok += 1
            print(f"{resultado.source:<16} {'OK':<7} {detalle}")
        else:
            print(f"{resultado.source:<16} {'FALLO':<7} {(resultado.error or '')[:58]}")
    print("-" * 78)
    print(f"[ingest] {ok}/{len(resultados)} fuentes con datos")

    if ok == 0:
        print(
            "[ingest] ninguna fuente produjo datos: el batch es un fracaso",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
