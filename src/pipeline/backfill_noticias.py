"""Recupera noticias históricas del archivo y las deposita en Bronze.

    docker compose exec -T app python -m src.pipeline.backfill_noticias \\
        --medio eleconomista --desde 2020-01 --hasta 2024-12
    ... --dry-run     # descubre y filtra sin descargar ni un artículo

**Un lote de Bronze por mes, y ahí termina.** A Silver se llega por `validate`,
igual que con cualquier otra fuente. Es un `ingest` con una ventana temporal
distinta, no un camino paralelo.

**Reanudable, porque va a tardar horas.** Antes de trabajar un mes comprueba si
ya existe su lote en Bronze y lo salta. Si el proceso muere a mitad de un
recorrido de cinco años —o alguien reinicia el servidor— se relanza el mismo
comando y sigue donde estaba. Esa es también la razón de escribir por mes en
lugar de acumular todo y guardar al final: un fallo en el mes 47 no puede
costar los 46 anteriores.

**Por qué tarda.** Se pide un artículo por segundo al Internet Archive, que
ofrece este servicio gratis y sin autenticación. Podría ir diez veces más
rápido; sería un uso abusivo de infraestructura pública mantenida con
donaciones. El filtro por titular ya reduce las descargas al 17%.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.config.emisoras import ALIAS_EMISORAS
from src.config.hemeroteca import MEDIOS
from src.pipeline.bronze import escribir_lote, leer_metadata, listar_lotes
from src.sources.hemeroteca import descubrir, menciona_emisora, recuperar


def _meses(desde: str, hasta: str) -> list[tuple[int, int]]:
    a1, m1 = (int(x) for x in desde.split("-"))
    a2, m2 = (int(x) for x in hasta.split("-"))
    salida = []
    a, m = a1, m1
    while (a, m) <= (a2, m2):
        salida.append((a, m))
        m += 1
        if m == 13:
            a, m = a + 1, 1
    return salida


def _ya_procesado(raiz: Path, source: str, anio: int, mes: int) -> bool:
    """¿Existe ya un lote de ese medio para ese mes?

    Se mira la ETIQUETA del lote, que se fija al mes recuperado y no al día en
    que se corrió. Así el reanudado funciona aunque el recorrido se reparta en
    varias sesiones y varios días.
    """
    etiqueta = date(anio, mes, 1).isoformat()
    for ruta in listar_lotes(raiz, categoria="news", source=source):
        meta = leer_metadata(ruta)
        if meta.get("fecha_lote") == etiqueta:
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backfill_noticias",
        description="Recupera noticias históricas del Internet Archive a Bronze.",
    )
    parser.add_argument("--medio", required=True, choices=sorted(MEDIOS))
    parser.add_argument("--desde", required=True, help="AAAA-MM inclusive")
    parser.add_argument("--hasta", required=True, help="AAAA-MM inclusive")
    parser.add_argument("--dry-run", action="store_true",
                        help="Descubre y filtra sin descargar ni escribir.")
    parser.add_argument("--rehacer", action="store_true",
                        help="No saltar los meses que ya tienen lote.")
    args = parser.parse_args(argv)

    medio = MEDIOS[args.medio]
    raiz = Path(get_settings().bronze_path)
    meses = _meses(args.desde, args.hasta)

    print(f"[hemeroteca] {medio.dominio} · {len(meses)} meses · "
          f"secciones: {', '.join(medio.secciones)}", flush=True)

    tot_vistos = tot_filtrados = tot_recuperados = tot_saltados = 0

    for anio, mes in meses:
        if not args.rehacer and not args.dry_run and _ya_procesado(raiz, medio.source, anio, mes):
            tot_saltados += 1
            continue

        hallazgos = []
        for seccion in medio.secciones:
            try:
                hallazgos += descubrir(medio.dominio, seccion, anio, mes,
                                       patron_fecha=medio.patron_fecha)
            except Exception as exc:  # noqa: BLE001
                print(f"[hemeroteca] {anio}-{mes:02d}/{seccion}: "
                      f"fallo al descubrir ({type(exc).__name__})", file=sys.stderr, flush=True)

        # Deduplica por URL: una misma nota aparece en varias secciones.
        unicos = {h.url: h for h in hallazgos}
        # Y descarta lo que el índice devolvió con fecha de otro mes: el CDX
        # filtra por fecha de CAPTURA, no de publicación.
        del_mes = [h for h in unicos.values() if h.fecha[:7] == f"{anio}-{mes:02d}"]
        relevantes = [h for h in del_mes if menciona_emisora(h.titular, ALIAS_EMISORAS)]

        tot_vistos += len(del_mes)
        tot_filtrados += len(relevantes)

        if args.dry_run:
            print(f"[hemeroteca] {anio}-{mes:02d}: {len(del_mes):>4} archivados · "
                  f"{len(relevantes):>3} con emisora", flush=True)
            for h in relevantes[:2]:
                print(f"                {h.fecha} · {h.titular[:70]}", flush=True)
            continue

        registros = [r for r in (recuperar(h) for h in relevantes) if r is not None]
        tot_recuperados += len(registros)

        if not registros:
            print(f"[hemeroteca] {anio}-{mes:02d}: sin artículos recuperables", flush=True)
            continue

        for r in registros:
            r["source"] = medio.source

        lote = escribir_lote(
            registros,
            source=medio.source,
            categoria="news",
            fecha=date(anio, mes, 1),
            raiz_bronze=raiz,
        )
        print(f"[hemeroteca] {anio}-{mes:02d}: {len(del_mes):>4} archivados · "
              f"{len(relevantes):>3} con emisora · {len(registros):>3} recuperados "
              f"· lote {str(lote.batch_uuid)[:8]}", flush=True)

    print(f"\n[hemeroteca] {tot_vistos} artículos vistos · {tot_filtrados} con emisora "
          f"({100 * tot_filtrados / tot_vistos:.0f}%)" if tot_vistos else "[hemeroteca] nada visto",
          flush=True)
    if tot_saltados:
        print(f"[hemeroteca] {tot_saltados} meses ya estaban en Bronze (usa --rehacer)", flush=True)
    if not args.dry_run:
        print(f"[hemeroteca] {tot_recuperados} artículos en Bronze · "
              "corre `validate` para llevarlos a Silver", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
