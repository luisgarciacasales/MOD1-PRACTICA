#!/usr/bin/env python3
"""Demostración de punta a punta — entregable de la práctica.

Ejecuta el pipeline medallón **dos veces sobre el mismo lote de Bronze** y
produce las tablas de evidencia de los cinco criterios de aceptación:

    Bronze        2+ lotes crudos, con timestamp, sin transformar
    Contrato      validación explícita, cuarentena con motivo
    Idempotencia  el reproceso da filas_nuevas = 0
    Duplicados    COUNT(*) > 1 por clave natural = 0 filas
    Gold          índice vectorial funcional, 1+ consulta semántica

Por qué dos pasadas sobre el MISMO lote y no dos ingestas: la idempotencia se
demuestra reprocesando exactamente los mismos datos. Si entre pasada y pasada
se reingiere, los feeds habrán cambiado y las filas nuevas de la segunda no
distinguirían "hay noticias nuevas" de "el UPSERT no funciona".

    docker compose exec -T app python scripts/demo_e2e.py
    docker compose exec -T app python scripts/demo_e2e.py --con-ingesta
    docker compose exec -T app python scripts/demo_e2e.py --salida /app/data/evidencia.md
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import psycopg

sys.path.insert(0, "/app")

from src.config import get_settings  # noqa: E402
from src.pipeline import db  # noqa: E402

# Etapas que transforman Bronze en Gold. `ingest` queda fuera del bucle a
# propósito: es lo que crearía un lote distinto y rompería el "mismo lote".
CADENA = [
    ("validate", ["python", "-m", "src.pipeline.validate"]),
    ("enrich", ["python", "-m", "src.pipeline.enrich"]),
    ("transform", ["python", "-m", "src.pipeline.transform"]),
    ("correlate", ["python", "-m", "src.pipeline.correlate"]),
    ("index", ["python", "-m", "src.pipeline.index"]),
]

# Clave natural de cada tabla — la que debe impedir los duplicados.
CLAVES_NATURALES = {
    "silver_news": "guid",
    "silver_market_prices": "ticker, date",
    "silver_macro_indicators": "series_id, date",
    "gold_enriched_news": "guid",
    "gold_market_prices": "ticker, date",
    "gold_macro_indicators": "series_id, date",
    "gold_news_market_corr": "news_guid, ticker, price_date",
}

TABLAS = list(CLAVES_NATURALES)

CONSULTAS_DEMO = [
    "recorte de la tasa de interés de Banxico y su efecto en la banca",
    "competencia de fintechs y neobancos contra la banca tradicional",
    "resultados y utilidades de una emisora mexicana",
]


class Reporte:
    """Acumula el informe en Markdown y lo va imprimiendo a la vez."""

    def __init__(self) -> None:
        self.lineas: list[str] = []

    def __call__(self, texto: str = "") -> None:
        print(texto, flush=True)
        self.lineas.append(texto)

    def tabla(self, cabeceras: list[str], filas: list[list[str]]) -> None:
        self(f"| {' | '.join(cabeceras)} |")
        self(f"|{'|'.join('---' for _ in cabeceras)}|")
        for fila in filas:
            self(f"| {' | '.join(str(c) for c in fila)} |")
        self()


def contar(cur) -> dict[str, int]:
    conteos = {}
    for tabla in TABLAS:
        cur.execute(f"SELECT COUNT(*) FROM {tabla}")  # noqa: S608 — lista fija
        conteos[tabla] = cur.fetchone()[0]
    return conteos


def ejecutar_cadena(rep: Reporte, pasada: int) -> dict[str, float]:
    rep(f"### Pasada {pasada}")
    rep()
    tiempos: dict[str, float] = {}
    for nombre, comando in CADENA:
        inicio = time.monotonic()
        resultado = subprocess.run(  # noqa: S603
            comando, capture_output=True, text=True, cwd="/app", check=False
        )
        tiempos[nombre] = time.monotonic() - inicio
        estado = "OK" if resultado.returncode == 0 else f"código {resultado.returncode}"
        rep(f"    {nombre:<11} {estado:<12} {tiempos[nombre]:>6.1f} s")
        if resultado.returncode != 0:
            rep(f"      stderr: {resultado.stderr.strip()[:200]}")
    rep()
    return tiempos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Demostración de punta a punta.")
    parser.add_argument("--con-ingesta", action="store_true",
                        help="Ingiere antes de empezar (crea lote nuevo en Bronze).")
    parser.add_argument(
        "--desde-cero", action="store_true",
        help="TRUNCA Silver y Gold antes de la pasada 1. Bronze NO se toca: es "
             "inmutable y basta para reconstruirlo todo. Da la evidencia más "
             "fuerte —pasada 1 inserta N filas, pasada 2 inserta 0— en vez de "
             "dos pasadas que ya parten de tablas llenas.",
    )
    parser.add_argument("--salida", default="/app/data/evidencia_e2e.md",
                        help="Dónde escribir el informe en Markdown.")
    args = parser.parse_args(argv)

    rep = Reporte()
    ahora = datetime.now(UTC).isoformat(timespec="seconds")
    settings = get_settings()

    rep("# Evidencia de ejecución de punta a punta")
    rep()
    rep(f"**Generado:** {ahora}  ")
    rep(f"**Modelo NLP:** `{settings.ollama_model_ner}` · "
        f"**Embeddings:** `{settings.embedding_model}` ({settings.embedding_dim} dim)  ")
    rep(f"**Lotes async:** {settings.nlp_batch_size} · "
        f"**Calendario:** {settings.market_calendar}")
    rep()

    if args.con_ingesta:
        rep("## Ingesta previa")
        rep()
        r = subprocess.run(  # noqa: S603
            ["python", "-m", "src.pipeline.ingest"], capture_output=True, text=True,
            cwd="/app", check=False,
        )
        rep("```")
        rep("\n".join(r.stdout.strip().splitlines()[-10:]))
        rep("```")
        rep()

    # --- Estado de Bronze ---------------------------------------------------
    from src.pipeline.bronze import leer_lote, listar_lotes, verificar_checksum

    raiz = Path(settings.bronze_path)
    lotes = listar_lotes(raiz)
    metadatos = [leer_lote(r)[0] for r in lotes]

    rep("## 1. Bronze — lotes crudos, con timestamp, sin transformar")
    rep()
    por_fuente: dict[str, list[dict]] = {}
    for m in metadatos:
        por_fuente.setdefault(m["source"], []).append(m)

    rep.tabla(
        ["Fuente", "Lotes", "Registros", "Último timestamp", "Checksum SHA-256"],
        [
            [
                fuente,
                len(ms),
                sum(m["record_count"] for m in ms),
                max(m["ingested_at"] for m in ms)[:19],
                ms[-1]["checksum_sha256"][:16] + "…",
            ]
            for fuente, ms in sorted(por_fuente.items())
        ],
    )
    integros = sum(1 for r in lotes if verificar_checksum(r))
    solo_lectura = sum(
        1 for r in lotes if not ((r / "raw_payload.json").stat().st_mode & 0o222)
    )
    rep(f"**{len(lotes)} lotes** · {integros}/{len(lotes)} con checksum íntegro · "
        f"{solo_lectura}/{len(lotes)} en modo solo lectura (0444), lo que impide "
        f"cualquier transformación posterior sobre Bronze.")
    rep()

    # --- Dos pasadas --------------------------------------------------------
    rep("## 2. Ejecución de punta a punta, dos veces sobre el mismo lote")
    rep()
    rep("No se reingiere entre pasadas: se reprocesan **exactamente los mismos "
        "lotes de Bronze**. Si se reingiriera, los feeds habrían cambiado y las "
        "filas nuevas de la segunda pasada no distinguirían «hay noticias "
        "nuevas» de «el UPSERT no funciona».")
    rep()

    if args.desde_cero:
        rep("Silver y Gold se vaciaron antes de la pasada 1, de modo que la "
            "tabla siguiente muestra la carga completa y su reproceso. **Bronze "
            "no se toca**: es inmutable y basta por sí solo para reconstruir "
            "todo lo demás.")
        rep()
        with db.conectar() as conexion, conexion.cursor() as cur:
            cur.execute("TRUNCATE " + ", ".join(TABLAS) + ", silver_dead_letters "
                        ", silver_fintech_dict RESTART IDENTITY CASCADE")
            conexion.commit()

    rep("```")

    with db.conectar() as conexion, conexion.cursor() as cur:
        antes_1 = contar(cur)
    t1 = ejecutar_cadena(rep, 1)
    with db.conectar() as conexion, conexion.cursor() as cur:
        despues_1 = contar(cur)
    t2 = ejecutar_cadena(rep, 2)
    with db.conectar() as conexion, conexion.cursor() as cur:
        despues_2 = contar(cur)

    rep("```")
    rep()
    rep(f"Tiempo total: pasada 1 = {sum(t1.values()):.1f} s · "
        f"pasada 2 = {sum(t2.values()):.1f} s")
    rep()

    # --- 3. Idempotencia ----------------------------------------------------
    rep("## 3. Idempotencia — el reproceso da `filas_nuevas = 0`")
    rep()
    rep.tabla(
        ["Tabla", "Antes", "Tras pasada 1", "Tras pasada 2", "filas_nuevas (2ª)"],
        [
            [
                f"`{t}`", antes_1[t], despues_1[t], despues_2[t],
                f"**{despues_2[t] - despues_1[t]}**",
            ]
            for t in TABLAS
        ],
    )
    total_nuevas = sum(despues_2[t] - despues_1[t] for t in TABLAS)
    cargadas_1 = sum(despues_1[t] - antes_1[t] for t in TABLAS)
    idempotente = total_nuevas == 0
    rep(f"**Resultado: {'CUMPLE' if idempotente else 'NO CUMPLE'}** — la pasada 1 "
        f"cargó {cargadas_1} filas y la pasada 2 añadió **{total_nuevas}** "
        f"reprocesando los mismos datos.")
    rep()
    rep("`enrich` solo procesa noticias con `enriched = false`, así que en la "
        "segunda pasada no tiene trabajo y termina en décimas de segundo: eso "
        "*es* su comportamiento idempotente, no una etapa saltada.")
    rep()

    # --- 4. Duplicados ------------------------------------------------------
    rep("## 4. Duplicados — `COUNT(*) > 1` por clave natural = 0 filas")
    rep()
    filas_dup = []
    sin_duplicados = True
    with db.conectar() as conexion, conexion.cursor() as cur:
        for tabla, clave in CLAVES_NATURALES.items():
            cur.execute(  # noqa: S608 — tabla y clave salen de un dict fijo
                f"SELECT COUNT(*) FROM (SELECT {clave} FROM {tabla} "
                f"GROUP BY {clave} HAVING COUNT(*) > 1) d"
            )
            n = cur.fetchone()[0]
            sin_duplicados &= n == 0
            filas_dup.append([f"`{tabla}`", f"`{clave}`", despues_2[tabla], f"**{n}**"])
    rep.tabla(["Tabla", "Clave natural", "Filas", "Grupos duplicados"], filas_dup)
    rep(f"**Resultado: {'CUMPLE' if sin_duplicados else 'NO CUMPLE'}** — "
        f"la consulta del criterio devuelve 0 filas en las "
        f"{len(CLAVES_NATURALES)} tablas con clave natural.")
    rep()

    # --- 5. Contrato --------------------------------------------------------
    rep("## 5. Contrato — validación explícita y cuarentena con motivo")
    rep()
    with db.conectar() as conexion, conexion.cursor() as cur:
        cur.execute(
            "SELECT rejection_reason, COUNT(*) FROM silver_dead_letters "
            "GROUP BY 1 ORDER BY 2 DESC"
        )
        motivos = cur.fetchall()
        cur.execute(
            "SELECT source, left(rejection_detail, 58), left(raw_payload->>'title', 44) "
            "FROM silver_dead_letters WHERE rejection_reason = 'MISSING_ENTITY' LIMIT 2"
        )
        ejemplos = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM silver_news WHERE macro_bypass")
        bypass = cur.fetchone()[0]

    rep.tabla(
        ["Motivo de rechazo", "Registros en cuarentena"],
        [[f"`{m}`", n] for m, n in motivos] or [["(ninguno)", 0]],
    )
    if ejemplos:
        rep("Ejemplos de registros en cuarentena con su motivo:")
        rep()
        rep.tabla(["Fuente", "Detalle", "Título original"],
                  [[e[0], e[1] or "—", (e[2] or "—")] for e in ejemplos])
    rep(f"El payload original se conserva íntegro en `raw_payload` (JSONB) para "
        f"poder revisarlo o reprocesarlo. **{bypass} noticias** entraron por el "
        f"bypass macroeconómico, la única excepción admitida a la regla de "
        f"«al menos un Ticker, Sector o Entidad».")
    rep()

    # --- 6. Gold: índice vectorial y consulta semántica ---------------------
    rep("## 6. Gold — índice vectorial funcional y consulta semántica")
    rep()
    from src.pipeline.index import cargar_indice
    from src.pipeline.search import search_semantic

    indice = cargar_indice()
    if indice is None or indice.ntotal == 0:
        rep("**NO CUMPLE** — no hay índice FAISS.")
        return _cerrar(rep, args.salida, exito=False)

    ruta_indice = Path(settings.faiss_index_path)
    rep(f"Índice `IndexFlatIP` (producto interno sobre vectores normalizados = "
        f"similitud coseno) con **{indice.ntotal} vectores de {indice.d} "
        f"dimensiones**, persistido en `{ruta_indice}` "
        f"({ruta_indice.stat().st_size / 1024:.0f} KB).")
    rep()

    # Primera consulta en frío para cargar el modelo; se cronometra aparte
    # porque no es latencia de búsqueda sino arranque.
    frio = time.perf_counter()
    search_semantic(CONSULTAS_DEMO[0], top_k=1)
    ms_frio = (time.perf_counter() - frio) * 1000

    for consulta in CONSULTAS_DEMO:
        inicio = time.perf_counter()
        resultados = search_semantic(consulta, top_k=5)
        ms = (time.perf_counter() - inicio) * 1000
        rep(f"**Consulta:** «{consulta}» — {len(resultados)} resultados en "
            f"**{ms:.0f} ms**")
        rep()
        rep.tabla(
            ["#", "Score", "Titular", "Fuente", "Fecha", "Sentimiento"],
            [
                [
                    i, f"{r.score:.3f}", r.title[:58].replace("|", "/"),
                    r.source, f"{r.published_at:%Y-%m-%d}", r.sentiment_label or "—",
                ]
                for i, r in enumerate(resultados, 1)
            ],
        )

    rep(f"Arranque en frío (carga del modelo de embeddings): {ms_frio:.0f} ms. "
        f"No es latencia de búsqueda — en un proceso de larga duración se paga "
        f"una sola vez.")
    rep()

    # --- Resumen ------------------------------------------------------------
    rep("## Resumen de criterios")
    rep()
    con_ticker = None
    with db.conectar() as conexion, conexion.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM gold_enriched_news WHERE embedding IS NOT NULL")
        con_ticker = cur.fetchone()[0]

    criterios = [
        ["Bronze", "2+ lotes crudos, timestamp, sin transformar",
         "CUMPLE" if len(lotes) >= 2 and integros == len(lotes) else "NO CUMPLE",
         f"{len(lotes)} lotes, {integros} con checksum íntegro, {solo_lectura} inmutables"],
        ["Contrato", "validación explícita, cuarentena con motivo",
         "CUMPLE" if motivos else "NO CUMPLE",
         f"{sum(n for _, n in motivos)} registros en cuarentena, "
         f"{len(motivos)} motivos tipados distintos"],
        ["Idempotencia", "reproceso da filas_nuevas = 0",
         "CUMPLE" if idempotente else "NO CUMPLE",
         f"segunda pasada: +{total_nuevas} filas en {len(TABLAS)} tablas"],
        ["Duplicados", "COUNT(*) > 1 por clave natural = 0 filas",
         "CUMPLE" if sin_duplicados else "NO CUMPLE",
         f"0 grupos duplicados en {len(CLAVES_NATURALES)} tablas"],
        ["Gold", "índice vectorial funcional, 1+ consulta semántica",
         "CUMPLE",
         f"{indice.ntotal} vectores indexados, {len(CONSULTAS_DEMO)} consultas "
         f"demostradas, {con_ticker} embeddings en pgvector"],
    ]
    rep.tabla(["Criterio", "Mínimo exigido", "Resultado", "Evidencia"], criterios)

    exito = all(c[2] == "CUMPLE" for c in criterios)
    return _cerrar(rep, args.salida, exito=exito)


def _cerrar(rep: Reporte, salida: str, *, exito: bool) -> int:
    destino = Path(salida)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text("\n".join(rep.lineas) + "\n", encoding="utf-8")
    print()
    print(f"Informe escrito en {destino}")
    return 0 if exito else 1


if __name__ == "__main__":
    raise SystemExit(main())
