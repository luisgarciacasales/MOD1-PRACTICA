"""Resumen diario de lo que hizo el pipeline, para enviar por Telegram.

    docker compose exec -T app python -m src.pipeline.resumen          # imprime
    docker compose exec -T app python -m src.pipeline.resumen --enviar # y avisa

**Por qué esto NO contradice la regla del silencio.** Los avisos de
`avisos.py` existen para interrumpir: algo falló y hay que actuar. Este resumen
es otra cosa — es un parte de jornada que se lee cuando uno quiere, no una
alarma. La regla que sí se mantiene es la que de verdad importa: **nunca decir
solo "todo correcto"**. Un mensaje ceremonial se ignora a la semana; uno con
cifras concretas se compara con el de ayer y delata lo que no cuadra.

Por eso el resumen lleva números y no adjetivos: cuántas noticias entraron,
cuántas correlaciones salieron, qué franjas corrieron y cuáles no. Si un día
aparece "0 noticias" o falta una franja, se ve sin necesidad de que nadie lo
califique de problema.
"""

from __future__ import annotations

import argparse
import re
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.config.tiempo import hoy_mercado
from src.pipeline import db


def _logs() -> Path:
    return Path(get_settings().bronze_path).parent / "logs"


def _franjas(dia: date) -> list[str]:
    """Qué franjas corrieron hoy, en orden, con su resultado."""
    ruta = _logs() / "scheduler.log"
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.startswith(dia.isoformat()) or "[scheduler]" not in linea:
            continue
        m = re.search(r"franja (\d{4}) — (OK|FALLÓ[^,]*)", linea)
        if m:
            hhmm = f"{m.group(1)[:2]}:{m.group(1)[2:]}"
            salida.append(f"{hhmm} {'OK' if m.group(2) == 'OK' else 'FALLÓ'}")
    return salida


def _corridas(dia: date) -> list[tuple[str, int, int]]:
    """(hora, segundos, noticias acumuladas) de cada corrida del día."""
    ruta = _logs() / "historial.log"
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if not linea.startswith(dia.isoformat()):
            continue
        hora = linea[11:16]
        total = int(m.group(1)) if (m := re.search(r"total=(\d+)s", linea)) else 0
        news = int(m.group(1)) if (m := re.search(r"news=(\d+)", linea)) else 0
        salida.append((hora, total, news))
    return salida


def componer(dia: date | None = None) -> tuple[str, str]:
    """Devuelve `(titulo, cuerpo)` del parte del día."""
    dia = dia or hoy_mercado()

    from src.pipeline.calendario import es_dia_habil

    habil = es_dia_habil(dia)
    franjas = _franjas(dia)
    corridas = _corridas(dia)

    with db.conectar() as cx, cx.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM silver_news")
        noticias = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM gold_news_market_corr")
        correlaciones = cur.fetchone()[0]
        cur.execute(
            "SELECT COUNT(*) FROM silver_news WHERE ingested_at::date = %s", (dia,)
        )
        nuevas_hoy = cur.fetchone()[0]

    L = []
    L.append(f"{'día hábil' if habil else 'día inhábil — solo noticias'}")
    L.append("")
    L.append("franjas:")
    L.append(f"  {' · '.join(franjas) if franjas else 'ninguna'}")
    if corridas:
        L.append("")
        L.append("corridas:")
        for hora, seg, _ in corridas:
            L.append(f"  {hora}  {seg:>4}s")
    L.append("")
    L.append(f"noticias nuevas hoy   {nuevas_hoy}")
    L.append(f"corpus                {noticias}")
    L.append(f"correlaciones         {correlaciones}")

    fallos = sum(1 for f in franjas if "FALLÓ" in f)
    titulo = f"Parte del {dia:%d-%b}"
    if fallos:
        titulo += f" — {fallos} franja(s) con fallo"
    return titulo, "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.resumen",
        description="Parte diario del pipeline.",
    )
    parser.add_argument("--enviar", action="store_true",
                        help="Además de imprimirlo, lo manda por Telegram.")
    parser.add_argument("--dia", default=None, help="YYYY-MM-DD (por defecto, hoy).")
    args = parser.parse_args(argv)

    dia = date.fromisoformat(args.dia) if args.dia else None
    titulo, cuerpo = componer(dia)
    print(f"{titulo}\n{cuerpo}")

    if args.enviar:
        from src.pipeline.avisos import enviar

        print("[resumen] enviado" if enviar(titulo, cuerpo) else "[resumen] no se pudo enviar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
