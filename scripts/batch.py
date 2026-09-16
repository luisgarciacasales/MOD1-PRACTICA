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
  historial con tiempos **y volúmenes**. Los volúmenes son lo que permite cruzar
  «el tiempo creció» con «el corpus creció»: sin ellos, una serie de tiempos no
  distingue una degradación de un aumento proporcional de trabajo.
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

sys.path.insert(0, "/app")

from src.config import get_settings  # noqa: E402
from src.config.tiempo import TZ_MERCADO, ahora_mercado  # noqa: E402
from src.pipeline.calendario import es_dia_habil  # noqa: E402

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

# Las mismas etapas en un día sin mercado, que son casi todas.
#
# En un día inhábil del sector financiero no hay precios nuevos ni indicadores
# que publicar, pero **los medios siguen publicando**. Saltarse la corrida
# entera costaría el hueco de noticias, y no es pequeño: entre el cierre de la
# víspera y la mañana siguiente se acumulan unas 36 horas, mientras que el feed
# de El Financiero solo conserva las últimas 100 entradas — unas 20 horas. Por
# diez inhábiles al año son unas 160 horas de corpus que no se recuperan luego,
# porque el archivo de prensa no las devuelve (ADR-20).
#
# Se omite `transform`, que deriva valuación de precios que hoy no han cambiado.
# `correlate` sí corre: las noticias de hoy se asocian al siguiente día hábil,
# que es precisamente lo que el calendario XMEX resuelve.
ETAPAS_SIN_MERCADO: tuple[tuple[str, list[str]], ...] = (
    ("ingest", ["python", "-m", "src.pipeline.ingest", "--solo-noticias"]),
    ("validate", ["python", "-m", "src.pipeline.validate"]),
    ("enrich", ["python", "-m", "src.pipeline.enrich"]),
    ("correlate", ["python", "-m", "src.pipeline.correlate"]),
    ("index", ["python", "-m", "src.pipeline.index"]),
)


def _ruta_logs() -> Path:
    # Junto a los datos, no en el repo: es salida de ejecución (invariante 1).
    ruta = Path(get_settings().bronze_path).parent / "logs"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def _volumenes() -> str:
    """Estado de las tablas tras la corrida, para la línea del historial.

    Se consulta al final en vez de parsear la salida de cada etapa: el conteo de
    las tablas es la verdad, y el texto de una etapa puede cambiar de formato.
    """
    from src.pipeline import db

    consultas = (
        ("news", "SELECT COUNT(*) FROM silver_news"),
        ("gold", "SELECT COUNT(*) FROM gold_enriched_news"),
        ("corr", "SELECT COUNT(*) FROM gold_news_market_corr"),
        ("proxy", "SELECT COUNT(*) FROM gold_news_market_corr WHERE is_proxy"),
        ("cuar", "SELECT COUNT(*) FROM silver_dead_letters"),
        ("precios", "SELECT COUNT(*) FROM silver_market_prices"),
        ("macro", "SELECT COUNT(*) FROM silver_macro_indicators"),
        ("fund", "SELECT COUNT(*) FROM silver_fundamentals"),
    )
    try:
        with db.conectar() as conexion, conexion.cursor() as cur:
            partes = []
            for etiqueta, sql in consultas:
                cur.execute(sql)
                partes.append(f"{etiqueta}={cur.fetchone()[0]}")
        return " ".join(partes)
    except Exception as exc:  # noqa: BLE001
        # Que falle el conteo no puede alterar el resultado del batch: es
        # instrumentación, no parte del pipeline.
        return f"volumenes=ERROR:{type(exc).__name__}"


def _mercado_cerrado(ahora: datetime) -> bool:
    """¿Se puede ingerir sin traer una vela a medio formar?

    El guard existe para una sola cosa: que no se archive en Bronze el precio
    de una sesión en curso como si fuera el cierre. Por tanto solo tiene
    sentido cuando HAY sesión.

    Sábado y domingo cuentan como cerrado, y también los inhábiles del sector
    financiero. Faltaba esto último y se notó el 16-sep-2026 —Día de la
    Independencia—: el equipo se encendió tarde, el `@reboot` recuperó
    correctamente la franja de las 08:00, y el batch la abortó con código 2
    diciendo que la BMV seguía abierta. Ese día no había BMV que estuviera
    abierta, y encima la corrida iba a ser de solo noticias, que no pide
    precios en absoluto.
    """
    if ahora.weekday() >= 5 or not es_dia_habil(ahora.date()):
        return True
    return ahora.hour >= HORA_CIERRE


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
        "--solo-noticias",
        action="store_true",
        help="Fuerza el modo de día sin mercado aunque hoy sea hábil.",
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

    # ¿Opera hoy el mercado? El calendario XMEX coincide con los días inhábiles
    # que publica la CNBV para 2026 —verificado uno a uno— y tiene la ventaja de
    # actualizarse con la librería en vez de con una lista a mano. Un test
    # contrasta ambos para que una divergencia futura no pase inadvertida.
    sin_mercado = args.solo_noticias or not es_dia_habil(ahora_mercado().date())

    base = ETAPAS_SIN_MERCADO if sin_mercado else ETAPAS
    if sin_mercado:
        print("[batch] día sin mercado: solo noticias "
              "(no hay precios ni indicadores nuevos que publicar)", flush=True)

    etapas = list(base)
    if args.hasta:
        nombres = [n for n, _ in base]
        if args.hasta in nombres:
            etapas = etapas[: nombres.index(args.hasta) + 1]

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
        tiempos = " ".join(f"{n}={s:.0f}s" for n, _, s in resultados)
        h.write(
            f"{ahora.isoformat(timespec='seconds')} {estado:<16} "
            f"total={total:.0f}s {tiempos} | {_volumenes()}\n"
        )

    print(f"\n[batch] log completo: {log}")
    print(f"[batch] historial:    {historial}")
    return 1 if fallo else 0


if __name__ == "__main__":
    raise SystemExit(main())
