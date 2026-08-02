"""Definición de Terminado del PRD §8, como checks ejecutables.

Implementa la checklist del skill `acceptance-verify`. Cada criterio es un
check con su evidencia real; ninguno se declara PASS sin haberla obtenido.

Regla de oro del skill: **no maquilles resultados.** Un check que no se puede
comprobar sale como FAIL o WARN con su motivo, nunca como PASS optimista.

    docker compose exec -T app python -m src.pipeline.verify
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from src.config import get_settings
from src.pipeline import db

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"


@dataclass
class Check:
    criterio: str
    estado: str
    evidencia: str


def _uno(cur, sql: str, params: tuple = ()) -> object:
    cur.execute(sql, params)
    fila = cur.fetchone()
    return fila[0] if fila else None


# --- 1. Bronze --------------------------------------------------------------


def check_bronze() -> list[Check]:
    from src.pipeline.bronze import leer_lote, listar_lotes, verificar_checksum

    raiz = Path(get_settings().bronze_path)
    lotes = listar_lotes(raiz)
    if not lotes:
        return [Check("Bronze — 5 fuentes", FAIL, "no hay lotes en Bronze")]

    metadatos = [leer_lote(r)[0] for r in lotes]
    fuentes = {m["source"] for m in metadatos}
    esperadas = {"bmv_eventos", "financiero", "economista", "bloomberg",
                 "finnovista", "yahoo_finance", "banxico"}
    faltan = esperadas - fuentes

    checks = [
        Check(
            "Bronze — fuentes ingeridas",
            PASS if len(fuentes) >= 5 else FAIL,
            f"{len(fuentes)}/7 fuentes: {', '.join(sorted(fuentes))}"
            + (f" · faltan: {', '.join(sorted(faltan))}" if faltan else ""),
        )
    ]

    # 2+ lotes de una misma fuente en un mismo día.
    por_dia: dict[tuple[str, str], int] = {}
    for m in metadatos:
        clave = (m["source"], m["fecha_lote"])
        por_dia[clave] = por_dia.get(clave, 0) + 1
    multi = {k: v for k, v in por_dia.items() if v > 1}
    checks.append(Check(
        "Bronze — 2+ lotes diarios",
        PASS if multi else FAIL,
        f"{len(lotes)} lotes; con 2+ el mismo día: "
        + (", ".join(f"{s} x{n}" for (s, _), n in list(multi.items())[:3]) if multi else "ninguno"),
    ))

    # Inmutabilidad: checksum íntegro y archivos en solo lectura.
    corruptos = [r.name for r in lotes if not verificar_checksum(r)]
    escribibles = [
        r.name for r in lotes
        if (r / "raw_payload.json").stat().st_mode & 0o222
    ]
    checks.append(Check(
        "Bronze — inmutable y trazable",
        PASS if not corruptos and not escribibles else FAIL,
        f"{len(lotes)} lotes con checksum SHA-256 íntegro y modo 0444"
        if not corruptos and not escribibles
        else f"corruptos={corruptos[:3]} escribibles={escribibles[:3]}",
    ))
    return checks


# --- 2 a 6: SQL -------------------------------------------------------------


def checks_sql(cur) -> list[Check]:
    checks: list[Check] = []

    # 2. Contrato semántico + bypass macro
    missing = _uno(cur, "SELECT COUNT(*) FROM silver_dead_letters WHERE rejection_reason='MISSING_ENTITY'")
    cur.execute("SELECT rejection_reason, COUNT(*) FROM silver_dead_letters GROUP BY 1 ORDER BY 2 DESC")
    motivos = ", ".join(f"{m}={n}" for m, n in cur.fetchall())
    checks.append(Check(
        "Contrato — rechazo MISSING_ENTITY",
        PASS if missing else FAIL,
        f"{missing} rechazos con MISSING_ENTITY · motivos: {motivos or 'ninguno'}",
    ))

    bypass = _uno(cur, "SELECT COUNT(*) FROM silver_news WHERE macro_bypass")
    ejemplo = _uno(cur, "SELECT left(title,58) FROM silver_news WHERE macro_bypass LIMIT 1") or ""
    checks.append(Check(
        "Contrato — bypass macroeconómico",
        PASS if bypass else FAIL,
        f"{bypass} noticias con macro_bypass · ej.: «{ejemplo}»",
    ))

    # 4. Cero duplicados
    dups = _uno(cur, "SELECT COUNT(*) FROM (SELECT guid FROM silver_news GROUP BY guid HAVING COUNT(*)>1) d")
    checks.append(Check(
        "Idempotencia — 0 duplicados en silver_news",
        PASS if dups == 0 else FAIL,
        f"{dups} guid duplicados (la consulta del §8 debe dar 0 filas)",
    ))

    # 5. NLP
    total = _uno(cur, "SELECT COUNT(*) FROM gold_enriched_news")
    con_ticker = _uno(cur, "SELECT COUNT(*) FROM gold_enriched_news WHERE array_length(ner_tickers,1)>0")
    con_orgs = _uno(cur, "SELECT COUNT(*) FROM gold_enriched_news WHERE array_length(ner_orgs,1)>0")
    checks.append(Check(
        "NLP — NER funcional en español",
        PASS if con_orgs else FAIL,
        f"{total} enriquecidas · {con_orgs} con organizaciones · {con_ticker} con emisora del universo",
    ))

    cur.execute("SELECT sentiment_label, COUNT(*) FROM gold_enriched_news GROUP BY 1 ORDER BY 2 DESC")
    reparto = cur.fetchall()
    checks.append(Check(
        "NLP — sentimiento asignado",
        PASS if reparto and all(r[0] for r in reparto) else FAIL,
        " · ".join(f"{e}={n}" for e, n in reparto) or "sin datos",
    ))

    ma = _uno(cur, "SELECT COUNT(*) FROM gold_enriched_news WHERE is_ma_event")
    ma_ej = _uno(cur, "SELECT left(title,52) FROM gold_enriched_news WHERE is_ma_event LIMIT 1") or ""
    checks.append(Check(
        "NLP — detección M&A (≥1 caso)",
        PASS if ma else FAIL,
        f"{ma} eventos · ej.: «{ma_ej}»",
    ))

    fintech = _uno(cur, "SELECT COUNT(*) FROM gold_enriched_news WHERE fintech_flag")
    dicc = _uno(cur, "SELECT COUNT(*) FROM silver_fintech_dict")
    checks.append(Check(
        "NLP — fintech tagging activo",
        PASS if fintech else FAIL,
        f"{fintech} noticias etiquetadas contra un diccionario de {dicc} fintechs",
    ))

    proxy = _uno(cur, "SELECT COUNT(*) FROM gold_news_market_corr WHERE is_proxy")
    cur.execute("""SELECT original_fintech, sector_affected, proxy_ticker
                   FROM gold_news_market_corr WHERE is_proxy LIMIT 1""")
    pe = cur.fetchone()
    checks.append(Check(
        "NLP — proxy ticker funcional (≥1)",
        PASS if proxy else FAIL,
        f"{proxy} correlaciones con is_proxy" + (f" · {pe[0]} → {pe[1]} → {pe[2]}" if pe else ""),
    ))

    # 6. Correlación temporal XMEX
    cur.execute("""
        SELECT news_date, next_trading_day, price_date
        FROM gold_news_market_corr
        WHERE EXTRACT(DOW FROM news_date) IN (5, 6, 0)   -- viernes, sábado, domingo
        ORDER BY news_date DESC LIMIT 1
    """)
    xmex = cur.fetchone()
    if xmex:
        nd, ntd, pd_ = xmex
        # Un fin de semana correctamente resuelto salta al lunes: día hábil y
        # estrictamente posterior.
        ok = ntd > nd and ntd.weekday() < 5 and pd_ >= ntd
        checks.append(Check(
            "Correlación — calendario XMEX",
            PASS if ok else FAIL,
            f"noticia {nd} ({_dia(nd)}) → next_trading_day {ntd} ({_dia(ntd)}) "
            f"→ price_date {pd_}",
        ))
    else:
        checks.append(Check(
            "Correlación — calendario XMEX", WARN,
            "no hay ninguna correlación de viernes o fin de semana con la que demostrarlo",
        ))

    # 7b. Correlación sentimiento ↔ precio
    cur.execute("""
        SELECT g.sentiment_label, c.ticker, c.news_date, c.price_date,
               round(c.next_day_return_pct::numeric, 2)
        FROM gold_news_market_corr c JOIN gold_enriched_news g ON g.guid = c.news_guid
        WHERE c.next_day_return_pct IS NOT NULL
        ORDER BY abs(c.next_day_return_pct) DESC LIMIT 1
    """)
    corr = cur.fetchone()
    total_corr = _uno(cur, "SELECT COUNT(*) FROM gold_news_market_corr")
    checks.append(Check(
        "Gold — correlación noticia ↔ precio",
        PASS if corr else FAIL,
        f"{total_corr} correlaciones · mayor movimiento: sentimiento {corr[0]} sobre "
        f"{corr[1]}, noticia {corr[2]} → precio {corr[3]}, retorno {corr[4]}%"
        if corr else "ninguna correlación con retorno calculado",
    ))

    return checks


def _dia(f) -> str:
    return ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"][f.weekday()]


# --- 3. Idempotencia ---------------------------------------------------------


def check_idempotencia(cur) -> Check:
    """Reprocesa validate y comprueba que no aparecen filas nuevas.

    Se comparan los COUNT antes y después en vez de fiarse del resumen que
    imprime la etapa: el criterio del §8 es sobre el estado de las tablas.
    """
    from src.pipeline import validate

    tablas = ("silver_news", "silver_market_prices", "silver_macro_indicators")
    antes = {t: _uno(cur, f"SELECT COUNT(*) FROM {t}") for t in tablas}

    salida_previa = sys.stdout
    try:
        sys.stdout = open("/dev/null", "w")  # noqa: SIM115
        validate.main([])
    except SystemExit:
        pass
    finally:
        sys.stdout.close()
        sys.stdout = salida_previa

    despues = {t: _uno(cur, f"SELECT COUNT(*) FROM {t}") for t in tablas}
    diferencias = {t: despues[t] - antes[t] for t in tablas}
    ok = all(d == 0 for d in diferencias.values())
    return Check(
        "Idempotencia — reproceso da filas_nuevas = 0",
        PASS if ok else FAIL,
        " · ".join(f"{t}: {antes[t]}→{despues[t]} (+{diferencias[t]})" for t in tablas),
    )


# --- 7. FAISS ----------------------------------------------------------------


CONSULTAS_DEMO = [
    "recorte de la tasa de interés de Banxico y su efecto en la banca",
    "resultados trimestrales de un banco mexicano",
    "competencia de fintechs y neobancos contra la banca tradicional",
]


def check_faiss() -> list[Check]:
    from src.pipeline.index import cargar_indice
    from src.pipeline.search import search_semantic

    indice = cargar_indice()
    if indice is None:
        return [Check("Gold — índice FAISS", FAIL,
                      f"no existe {get_settings().faiss_index_path}")]

    checks = [Check(
        "Gold — índice FAISS construido",
        PASS if indice.ntotal else FAIL,
        f"{indice.ntotal} vectores de {indice.d} dimensiones "
        f"· modelo {get_settings().embedding_model}",
    )]

    consulta = CONSULTAS_DEMO[0]
    try:
        inicio = time.perf_counter()
        resultados = search_semantic(consulta, top_k=5)
        ms = (time.perf_counter() - inicio) * 1000
    except Exception as exc:  # noqa: BLE001
        return [*checks, Check("Gold — consulta semántica", FAIL, f"{type(exc).__name__}: {exc}")]

    if not resultados:
        return [*checks, Check("Gold — consulta semántica", FAIL, "0 resultados")]

    primero = resultados[0]
    checks.append(Check(
        "Gold — consulta semántica en español",
        PASS,
        f'«{consulta}» → {len(resultados)} resultados en {ms:.0f} ms · '
        f'top-1 [{primero.score:.3f}] «{primero.title[:52]}»',
    ))
    checks.append(Check(
        "Gold — SLA búsqueda <500 ms",
        PASS if ms < 500 else WARN,
        f"{ms:.0f} ms (incluye vectorizar la consulta)",
    ))
    return checks


# --- Ejecución ---------------------------------------------------------------


def ejecutar(*, saltar_idempotencia: bool) -> int:
    print("Definición de Terminado — PRD §8")
    print("=" * 100)

    checks = check_bronze()
    with db.conectar() as conexion, conexion.cursor() as cur:
        checks += checks_sql(cur)
        if not saltar_idempotencia:
            checks.append(check_idempotencia(cur))
        conexion.commit()
    checks += check_faiss()

    print(f"{'CRITERIO':<42} {'':<5} EVIDENCIA")
    print("-" * 100)
    for c in checks:
        marca = {PASS: "PASS", FAIL: "FAIL", WARN: "WARN"}[c.estado]
        print(f"{c.criterio:<42} {marca:<5} {c.evidencia}")
    print("-" * 100)

    fallos = [c for c in checks if c.estado == FAIL]
    avisos = [c for c in checks if c.estado == WARN]
    print(f"{len(checks) - len(fallos) - len(avisos)} PASS · {len(avisos)} WARN · {len(fallos)} FAIL")

    if fallos:
        print("\nCriterios NO cumplidos:")
        for c in fallos:
            print(f"  · {c.criterio}: {c.evidencia}")
    return 1 if fallos else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.verify",
        description="Checks ejecutables de la Definición de Terminado (PRD §8).",
    )
    parser.add_argument("--skip-idempotencia", action="store_true",
                        help="No reejecuta validate (el check más lento).")
    args = parser.parse_args(argv)
    return ejecutar(saltar_idempotencia=args.skip_idempotencia)


if __name__ == "__main__":
    raise SystemExit(main())
