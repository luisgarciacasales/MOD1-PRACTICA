#!/usr/bin/env python3
"""Batch diario: encadena las seis etapas del pipeline en orden.

Es lo que se ejecuta a mano durante el periodo de supervisión y lo que llamará
el cron después. Que sea la misma pieza en los dos casos es deliberado: cuando
se automatice, lo que corra ya llevará días probado.

    docker compose exec -T app python scripts/batch.py
    docker compose exec -T app python scripts/batch.py --ignorar-horario
    docker compose exec -T app python scripts/batch.py --hasta correlate

Tres garantías:

· **Aborta en el primer fallo.** Si `validate` falla, no se ejecutan `enrich` ni
  las siguientes. Continuar dejaría Gold construido a medias sobre un Silver
  incompleto, y eso es peor que no tener el dato del día: el estado parcial no se
  distingue del completo al mirar las tablas.
· **Deja rastro.** Cada corrida escribe su log completo y una línea en el
  historial, para poder auditar qué pasó un día concreto sin depender de haber
  estado mirando la consola.
· **No corre antes del cierre.** La BMV cierra a las 15:00 CT y el PRD §4.4 fija
  la ingesta post-cierre. Ejecutarlo antes trae una vela incompleta del día en
  curso, que además contamina el cálculo de retornos.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/app")

from src.config import get_settings  # noqa: E402

TZ_MERCADO = ZoneInfo("America/Mexico_City")
HORA_CIERRE = 15  # BMV: 15:00 CT

# Orden no negociable: cada etapa consume lo que produjo la anterior.
ETAPAS: tuple[tuple[str, list[str]], ...] = (
    ("ingest", ["python", "-m", "src.pipeline.ingest"]),
    ("validate", ["python", "-m", "src.pipeline.validate"]),
    ("enrich", ["python", "-m", "src.pipeline.enrich"]),
    ("transform", ["python", "-m", "src.pipeline.transform"]),
    ("correlate", ["python", "-m", "src.pipeline.correlate"]),
    ("index", ["python", "-m", "src.pipeline.index"]),
)


def _ruta_logs() -> Path:
    # Junto a los datos, no en el repo: es salida de ejecución (invariante 1).
    ruta = Path(get_settings().bronze_path).parent / "logs"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _mercado_cerrado(ahora: datetime) -> bool:
    # Sábado y domingo cuentan como cerrado: no habrá vela nueva, pero tampoco
    # una incompleta, así que el batch puede correr sin riesgo.
    return ahora.weekday() >= 5 or ahora.hour >= HORA_CIERRE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="scripts/batch.py",
        description="Encadena las seis etapas del pipeline medallón.",
    )
    parser.add_argument(
        "--ignorar-horario", action="store_true",
        help="Corre aunque la BMV siga abierta. Trae una vela incompleta del día.",
    )
    parser.add_argument(
        "--hasta", choices=[n for n, _ in ETAPAS], default=None,
        help="Detiene la cadena tras esta etapa.",
    )
    args = parser.parse_args(argv)

    ahora = datetime.now(TZ_MERCADO)
    marca = ahora.strftime("%Y-%m-%d_%H%M")

    if not _mercado_cerrado(ahora) and not args.ignorar_horario:
        print(
            f"[batch] ABORTADO — son las {ahora:%H:%M} CT y la BMV cierra a las "
            f"{HORA_CIERRE}:00.\n"
            "        Ingerir ahora traería una vela incompleta del día en curso y "
            "contaminaría\n"
            "        el cálculo de retornos. Usa --ignorar-horario si de verdad lo "
            "quieres.",
            file=sys.stderr,
        )
        return 2

    etapas = list(ETAPAS)
    if args.hasta:
        corte = [n for n, _ in ETAPAS].index(args.hasta)
        etapas = etapas[: corte + 1]

    log = _ruta_logs() / f"batch_{marca}.log"
    historial = _ruta_logs() / "historial.log"

    resultados: list[tuple[str, int, float]] = []
    fallo: str | None = None

    with log.open("w", encoding="utf-8") as f:
        f.write(f"batch {ahora.isoformat()}  ({len(etapas)} etapas)\n")
        f.write("=" * 78 + "\n")
        print(f"[batch] {ahora:%Y-%m-%d %H:%M} CT · {len(etapas)} etapas · log {log.name}")

        for nombre, comando in etapas:
            inicio = datetime.now()
            print(f"[batch] {nombre}…", flush=True)
            proceso = subprocess.run(  # noqa: S603
                comando, capture_output=True, text=True, cwd="/app", check=False
            )
            segundos = (datetime.now() - inicio).total_seconds()
            resultados.append((nombre, proceso.returncode, segundos))

            f.write(f"\n{'-' * 78}\n### {nombre}  ({segundos:.1f} s, código {proceso.returncode})\n")
            f.write(proceso.stdout)
            if proceso.stderr:
                f.write("\n--- stderr ---\n" + proceso.stderr)
            f.flush()

            estado = "OK" if proceso.returncode == 0 else f"código {proceso.returncode}"
            print(f"[batch]   {estado:<12} {segundos:>6.1f} s", flush=True)

            if proceso.returncode != 0:
                # Abortar y no seguir: un Gold a medias sobre un Silver
                # incompleto no se distingue de uno completo al mirar las tablas.
                fallo = nombre
                print(
                    f"[batch] ABORTADO en '{nombre}'. Las etapas siguientes NO se "
                    f"ejecutan.\n[batch] Detalle en {log}",
                    file=sys.stderr,
                )
                if proceso.stderr.strip():
                    print(f"[batch] stderr: {proceso.stderr.strip()[:300]}", file=sys.stderr)
                break

    total = sum(s for _, _, s in resultados)
    print()
    print(f"{'ETAPA':<12} {'ESTADO':<10} {'SEGUNDOS':>9}")
    print("-" * 34)
    for nombre, codigo, segundos in resultados:
        print(f"{nombre:<12} {'OK' if codigo == 0 else 'FALLO':<10} {segundos:>9.1f}")
    print("-" * 34)
    print(f"{'TOTAL':<12} {'':<10} {total:>9.1f}")

    # Una línea por corrida: es lo que permite ver la estabilidad entre días sin
    # abrir seis logs.
    with historial.open("a", encoding="utf-8") as h:
        estado = "OK" if fallo is None else f"FALLO:{fallo}"
        detalle = " ".join(f"{n}={s:.0f}s" for n, _, s in resultados)
        h.write(f"{ahora.isoformat(timespec='seconds')} {estado:<16} total={total:.0f}s {detalle}\n")

    print(f"\n[batch] log completo: {log}")
    print(f"[batch] historial:    {historial}")
    return 1 if fallo else 0


if __name__ == "__main__":
    raise SystemExit(main())
