"""Etapa F3 — ¿la señal predice retorno futuro? (roadmap, fase 3).

Antes de presentar un múltiplo como insumo de decisión hay que demostrar que
anticipa algo. Este módulo no calcula señales: las toma de `gold_valuation` y
mide qué pasó DESPUÉS.

    docker compose exec -T app python -m src.pipeline.backtest
    docker compose exec -T app python -m src.pipeline.backtest --horizonte 20

Cómo está construido, y por qué de esa forma:

· **Retorno forward, nunca contemporáneo.** La señal en `t` se cruza con el
  retorno de `t` a `t+h`. Medirla contra el retorno del mismo día solo
  demostraría que el precio está en el numerador del múltiplo.
· **Exceso sobre ^MXX, no retorno bruto.** Sin restar el mercado, cualquier
  señal parecería funcionar en un año alcista: estaría midiendo beta.
· **Terciles cross-sectional por fecha.** Se compara a las emisoras entre sí en
  el MISMO día. Ordenar sobre toda la muestra mezclaría regímenes: un P/U de
  2023 y uno de 2026 no son comparables sin más. Terciles y no quintiles porque
  con 16 emisoras un quintil son tres nombres.
· **n_efectiva = n / horizonte.** Los retornos a 20 días medidos a diario se
  solapan 19/20. La n cruda infla cualquier prueba de significancia, así que se
  reporta la corregida al lado.

Lo que este backtest NO puede decir, y conviene tener presente al leerlo:

· **Sesgo de supervivencia.** El universo son las 16 emisoras vivas hoy. Las que
  quebraron o dejaron de cotizar no están, y eso infla cualquier retorno.
· **Un solo mercado y 3,5 años.** No hay validación fuera de muestra ni otro
  régimen contra el que contrastar.
· **Pruebas múltiples.** Se evalúan tres señales por tres horizontes: nueve
  combinaciones. A ese ritmo, una «significativa» por azar es lo esperable.
  Por eso el criterio de lectura es la MONOTONÍA entre terciles, no que un
  spread suelto salga positivo.
"""

from __future__ import annotations

import argparse

from src.pipeline import db

# Señal → (columna, descripción). En las tres, valor BAJO = barato, así que el
# tercil 1 es siempre el extremo barato y el spread se lee igual en todas.
SENALES: dict[str, str] = {
    "pe_zscore_1y": "P/U contra su propia historia de 1 año",
    "pb_zscore_1y": "P/VL contra su propia historia de 1 año",
    "pe_premium_sector_pct": "P/U contra la mediana de su sector",
}

HORIZONTES = (5, 20, 60)

_SQL = """
WITH precios AS (
    SELECT ticker, date, adj_close,
           LEAD(adj_close, %(h)s) OVER (PARTITION BY ticker ORDER BY date) AS precio_fwd
    FROM gold_market_prices
    WHERE ticker <> %(benchmark)s
),
bench AS (
    SELECT date, adj_close,
           LEAD(adj_close, %(h)s) OVER (ORDER BY date) AS precio_fwd
    FROM gold_market_prices
    WHERE ticker = %(benchmark)s
),
-- Exceso de retorno en el horizonte: lo que hizo la emisora menos lo que hizo
-- el índice en la MISMA ventana.
exceso AS (
    SELECT p.ticker, p.date,
           100.0 * (p.precio_fwd / NULLIF(p.adj_close, 0) - 1)
         - 100.0 * (b.precio_fwd / NULLIF(b.adj_close, 0) - 1) AS exceso_fwd
    FROM precios p
    JOIN bench b ON b.date = p.date
    WHERE p.precio_fwd IS NOT NULL AND b.precio_fwd IS NOT NULL
),
senal AS (
    SELECT v.ticker, v.date, v.{columna} AS valor, e.exceso_fwd
    FROM gold_valuation v
    JOIN exceso e ON e.ticker = v.ticker AND e.date = v.date
    WHERE v.{columna} IS NOT NULL
),
-- Terciles dentro de cada FECHA: compara emisoras entre sí, no contra otros
-- regímenes. Se exige un mínimo de emisoras ese día para que el tercil
-- signifique algo.
con_tercil AS (
    SELECT s.*,
           NTILE(3) OVER (PARTITION BY s.date ORDER BY s.valor) AS tercil
    FROM senal s
    JOIN (
        SELECT date FROM senal GROUP BY date HAVING COUNT(*) >= %(min_emisoras)s
    ) d ON d.date = s.date
)
SELECT tercil,
       COUNT(*)                       AS n,
       AVG(exceso_fwd)                AS exceso_medio,
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY exceso_fwd) AS exceso_mediano,
       STDDEV_SAMP(exceso_fwd)        AS desv,
       AVG(valor)                     AS senal_media,
       MIN(date)                      AS desde,
       MAX(date)                      AS hasta
FROM con_tercil
GROUP BY tercil
ORDER BY tercil
"""

_SQL_GUARDAR = """
INSERT INTO gold_backtest_senal (
    senal, horizonte_dias, tercil, n, n_efectiva,
    exceso_medio, exceso_mediano, desv_exceso, senal_media, desde, hasta, calculado_at
) VALUES (
    %(senal)s, %(h)s, %(tercil)s, %(n)s, %(n_efectiva)s,
    %(exceso_medio)s, %(exceso_mediano)s, %(desv)s, %(senal_media)s,
    %(desde)s, %(hasta)s, NOW()
)
ON CONFLICT (senal, horizonte_dias, tercil) DO UPDATE SET
    n              = EXCLUDED.n,
    n_efectiva     = EXCLUDED.n_efectiva,
    exceso_medio   = EXCLUDED.exceso_medio,
    exceso_mediano = EXCLUDED.exceso_mediano,
    desv_exceso    = EXCLUDED.desv_exceso,
    senal_media    = EXCLUDED.senal_media,
    desde          = EXCLUDED.desde,
    hasta          = EXCLUDED.hasta,
    calculado_at   = NOW()
"""

# Con menos de 6 emisoras ese día, un tercil son dos nombres.
MIN_EMISORAS_POR_FECHA = 6


def _veredicto(filas: list[dict]) -> str:
    """Lee el resultado sin adornarlo.

    El criterio es la MONOTONÍA, no que el spread salga positivo: con nueve
    combinaciones evaluadas, un spread suelto a favor es lo que cabe esperar
    por azar. Que el tercil medio quede ordenado entre los extremos es mucho
    más difícil de conseguir por casualidad.
    """
    if len(filas) != 3:
        return "SIN DATOS"
    barato, medio, caro = (f["exceso_medio"] for f in filas)
    spread = barato - caro
    monotona = barato > medio > caro or barato < medio < caro
    if not monotona:
        return f"SIN SEÑAL (no monótona, spread {spread:+.2f} pp)"
    if barato > caro:
        return f"a favor: barato supera a caro por {spread:+.2f} pp, monótona"
    return f"EN CONTRA: barato rinde {spread:+.2f} pp vs caro, monótona"


def ejecutar(horizontes: tuple[int, ...] = HORIZONTES) -> int:
    from src.config.tickers import BENCHMARK

    with db.conectar() as conexion, conexion.cursor() as cur:
        for senal, descripcion in SENALES.items():
            print(f"\n{'=' * 78}")
            print(f"{senal} — {descripcion}")
            print("=" * 78)

            for h in horizontes:
                cur.execute(
                    _SQL.format(columna=senal),
                    {"h": h, "benchmark": BENCHMARK,
                     "min_emisoras": MIN_EMISORAS_POR_FECHA},
                )
                filas = [
                    {"tercil": f[0], "n": f[1], "exceso_medio": f[2],
                     "exceso_mediano": f[3], "desv": f[4], "senal_media": f[5],
                     "desde": f[6], "hasta": f[7]}
                    for f in cur.fetchall()
                ]
                if not filas:
                    print(f"\n  horizonte {h}d: sin observaciones")
                    continue

                print(f"\n  horizonte {h} días hábiles      "
                      f"({filas[0]['desde']} → {filas[0]['hasta']})")
                print(f"  {'TERCIL':<10}{'N':>7}{'N_EFEC':>8}{'SEÑAL':>9}"
                      f"{'EXCESO MEDIO':>14}{'MEDIANO':>10}")
                etiquetas = {1: "1 barato", 2: "2 medio", 3: "3 caro"}
                for f in filas:
                    n_efectiva = max(1, f["n"] // h)
                    print(f"  {etiquetas[f['tercil']]:<10}{f['n']:>7}{n_efectiva:>8}"
                          f"{f['senal_media']:>9.2f}"
                          f"{f['exceso_medio']:>13.2f}%{f['exceso_mediano']:>9.2f}%")
                    cur.execute(_SQL_GUARDAR, {
                        "senal": senal, "h": h, "tercil": f["tercil"],
                        "n": f["n"], "n_efectiva": n_efectiva,
                        "exceso_medio": f["exceso_medio"],
                        "exceso_mediano": f["exceso_mediano"],
                        "desv": f["desv"], "senal_media": f["senal_media"],
                        "desde": f["desde"], "hasta": f["hasta"],
                    })
                print(f"  → {_veredicto(filas)}")
        conexion.commit()

    print(f"\n{'=' * 78}")
    print("Cómo leer esto — límites que NO se resuelven con más cómputo:")
    print("  · Sesgo de supervivencia: son las 16 emisoras vivas HOY.")
    print("  · 3,5 años, un solo mercado, sin validación fuera de muestra.")
    print("  · 9 combinaciones evaluadas: un spread suelto a favor es lo")
    print("    esperable por azar. El criterio es la monotonía entre terciles.")
    print("  · Retornos solapados: n_efectiva ya lo corrige de forma burda.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backtest",
        description="F3 — mide si las señales de valuación anticipan exceso de retorno.",
    )
    parser.add_argument(
        "--horizonte", type=int, action="append", dest="horizontes",
        help="Días hábiles hacia adelante; repetible. Por defecto 5, 20 y 60.",
    )
    args = parser.parse_args(argv)
    return ejecutar(tuple(args.horizontes) if args.horizontes else HORIZONTES)


if __name__ == "__main__":
    raise SystemExit(main())
