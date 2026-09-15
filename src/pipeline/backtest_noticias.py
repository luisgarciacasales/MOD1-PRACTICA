"""¿El sentimiento de las noticias anticipa el retorno? — la pregunta que F3 dejó abierta.

    docker compose exec -T app python -m src.pipeline.backtest_noticias

F3 evaluó las tres señales de valuación y ninguna predijo nada (ADR del
27-ago-2026). La señal de noticias quedó sin evaluar por falta de muestra: en
aquel momento, 413 de 520 correlaciones eran de un solo mes. Con el corpus
actual ya hay material para intentarlo.

**Por qué no se reutiliza `backtest.py`.** Aquel mide señales CONTINUAS por
emisora y día, ordenándolas en terciles cross-sectional. Una noticia es un
EVENTO discreto: no existe «el sentimiento de Banorte el martes» sino noticias
sueltas en momentos sueltos. Es un estudio de eventos, y mezclarlo con el otro
marco habría forzado la señal a una forma que no tiene.

Se conservan las decisiones metodológicas que sí trasladan:

· **Retorno forward, nunca contemporáneo.** Se mide desde el siguiente día
  hábil a la publicación —que es lo que ya resuelve `next_trading_day`— hacia
  adelante. Medir el mismo día diría que la noticia y el precio se movieron a la
  vez, no que uno anticipa al otro.
· **Exceso sobre ^MXX.** Sin restar el mercado, un día en que sube todo haría
  parecer buena cualquier noticia. Se mide contra el índice en el MISMO tramo.
· **n efectiva.** Las noticias del mismo día sobre la misma emisora comparten
  casi todo su retorno: no son observaciones independientes. Se reporta cuántos
  pares (emisora, día) distintos hay detrás de cada grupo, que es la cifra
  honesta para juzgar significancia.

**Y una advertencia propia de esta señal:** el sentimiento tiene una
variabilidad medida del 18% entre reprocesos (ADR-18) cuya causa sigue sin
explicarse. Parte de cualquier ordenación que se vea aquí podría moverse al
reprocesar el corpus. Cualquier conclusión debe leerse con eso delante.
"""

from __future__ import annotations

import argparse

from src.pipeline import db

HORIZONTES = (1, 5, 20)
ORDEN = ("negative", "neutral", "positive")

_SQL = """
WITH eventos AS (
    SELECT c.ticker,
           c.price_date                      AS dia0,
           g.sentiment_label                 AS sentimiento,
           c.news_guid
    FROM gold_news_market_corr c
    JOIN gold_enriched_news g ON g.guid = c.news_guid
    WHERE NOT c.is_proxy
      AND g.sentiment_label IS NOT NULL
      AND c.price_date IS NOT NULL
),
-- Retorno de la emisora entre el día del evento y +h días hábiles, usando
-- posiciones de la propia serie: así el horizonte cuenta sesiones y no días
-- naturales, que es lo que hace comparable un puente con una semana normal.
con_retorno AS (
    SELECT e.*,
           p0.adj_close AS precio0,
           ph.adj_close AS precioh,
           m0.adj_close AS indice0,
           mh.adj_close AS indiceh
    FROM eventos e
    JOIN LATERAL (
        SELECT adj_close FROM silver_market_prices
        WHERE ticker = e.ticker AND date = e.dia0
    ) p0 ON TRUE
    JOIN LATERAL (
        SELECT adj_close FROM silver_market_prices
        WHERE ticker = e.ticker AND date > e.dia0
        ORDER BY date OFFSET %(h)s - 1 LIMIT 1
    ) ph ON TRUE
    JOIN LATERAL (
        SELECT adj_close FROM silver_market_prices
        WHERE ticker = %(benchmark)s AND date <= e.dia0
        ORDER BY date DESC LIMIT 1
    ) m0 ON TRUE
    JOIN LATERAL (
        SELECT adj_close FROM silver_market_prices
        WHERE ticker = %(benchmark)s AND date > e.dia0
        ORDER BY date OFFSET %(h)s - 1 LIMIT 1
    ) mh ON TRUE
)
SELECT sentimiento,
       COUNT(*)                                            AS n,
       COUNT(DISTINCT (ticker, dia0))                      AS n_efectiva,
       ROUND(AVG(100.0 * (precioh / precio0 - 1)
                 - 100.0 * (indiceh / indice0 - 1))::numeric, 3) AS exceso_medio,
       ROUND(STDDEV_SAMP(100.0 * (precioh / precio0 - 1)
                 - 100.0 * (indiceh / indice0 - 1))::numeric, 2) AS desv
FROM con_retorno
GROUP BY sentimiento
"""


def _significancia(por_sent: dict[str, dict]) -> tuple[float, float, float]:
    """Spread positive−negative, su error estándar y el estadístico t.

    Sin esto, la monotonía engaña: un orden correcto entre tres medias es fácil
    de obtener por azar cuando las desviaciones típicas son varias veces mayores
    que las diferencias. El error se calcula sobre la **n efectiva** —pares
    (emisora, día) distintos— y no sobre la cruda, porque varias noticias del
    mismo día comparten retorno y contarlas por separado estrecharía
    artificialmente el intervalo.
    """
    import math

    p, n = por_sent.get("positive"), por_sent.get("negative")
    if not p or not n:
        return (0.0, 0.0, 0.0)

    spread = float(p["exceso_medio"]) - float(n["exceso_medio"])
    ep = float(p["desv"] or 0) / math.sqrt(max(p["n_efectiva"], 1))
    en = float(n["desv"] or 0) / math.sqrt(max(n["n_efectiva"], 1))
    error = math.sqrt(ep**2 + en**2)
    return (spread, error, spread / error if error else 0.0)


def _veredicto(por_sent: dict[str, dict]) -> str:
    """Se exige MONOTONÍA, no un spread favorable.

    Con tres grupos y tres horizontes hay nueve comparaciones: que una salga
    a favor por azar es lo esperable. Que el orden negative < neutral < positive
    se respete es mucho menos probable de obtener por casualidad, y es además lo
    que tendría que ocurrir si la señal fuese real.
    """
    faltan = [s for s in ORDEN if s not in por_sent]
    if faltan:
        return f"sin datos suficientes ({', '.join(faltan)})"

    valores = [float(por_sent[s]["exceso_medio"]) for s in ORDEN]
    if valores[0] < valores[1] < valores[2]:
        spread = valores[2] - valores[0]
        return f"MONÓTONA a favor · spread {spread:+.2f} pp"
    if valores[0] > valores[1] > valores[2]:
        return f"MONÓTONA EN CONTRA · spread {valores[2] - valores[0]:+.2f} pp"
    return "sin orden: no monótona"


def ejecutar(horizontes: tuple[int, ...] = HORIZONTES) -> int:
    from src.config.tickers import BENCHMARK

    print("Backtest de la señal de noticias — exceso sobre el índice")
    print("=" * 74)

    with db.conectar() as cx, cx.cursor() as cur:
        for h in horizontes:
            cur.execute(_SQL, {"h": h, "benchmark": BENCHMARK})
            filas = cur.fetchall()
            por_sent = {
                f[0]: {"n": f[1], "n_efectiva": f[2], "exceso_medio": f[3], "desv": f[4]}
                for f in filas
            }

            print()
            print(f"Horizonte de {h} sesión(es)")
            print(f"  {'sentimiento':<12}{'n':>6}{'n efectiva':>12}"
                  f"{'exceso medio':>15}{'desv':>9}")
            print("  " + "-" * 54)
            for s in ORDEN:
                d = por_sent.get(s)
                if not d:
                    print(f"  {s:<12}{'—':>6}")
                    continue
                print(f"  {s:<12}{d['n']:>6}{d['n_efectiva']:>12}"
                      f"{float(d['exceso_medio']):>14.3f}%{float(d['desv'] or 0):>8.2f}")
            spread, error, t_stat = _significancia(por_sent)
            # |t| < 2 es, a ojo, no distinguible de cero al 95%. Se evita
            # hablar de "significativo" sin más: con esta muestra el valor de
            # esta línea es acotar la magnitud del ruido, no bendecir nada.
            juicio = ("dentro del ruido" if abs(t_stat) < 2
                      else "fuera del ruido — merece mirarse")
            print(f"  → {_veredicto(por_sent)}")
            print(f"     spread {spread:+.2f} pp ± {error:.2f} (t={t_stat:+.2f}) · {juicio}")

    print()
    print("Cómo leer esto")
    print("-" * 74)
    print("  · 'exceso medio' ya descuenta el índice: es lo que hizo la emisora POR")
    print("    ENCIMA del mercado en ese tramo.")
    print("  · 'n efectiva' cuenta pares (emisora, día) distintos. Varias noticias del")
    print("    mismo día sobre la misma emisora comparten retorno y no son")
    print("    observaciones independientes.")
    print("  · El criterio es la MONOTONÍA, no que un spread salga positivo: con nueve")
    print("    comparaciones, una favorable por azar es lo esperable.")
    print("  · El sentimiento varía un 18% entre reprocesos por causa no explicada")
    print("    (ADR-18). Parte de lo que se vea aquí podría moverse.")
    print("  · Un |t| por debajo de 2 significa que el spread no se distingue de cero:")
    print("    la ordenación puede ser real o puede ser azar, y esta muestra no")
    print("    permite separarlo. No es lo mismo que haber demostrado que no sirve.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.backtest_noticias",
        description="¿El sentimiento de las noticias anticipa el retorno?",
    )
    parser.add_argument("--horizonte", type=int, action="append", dest="horizontes")
    args = parser.parse_args(argv)
    return ejecutar(tuple(args.horizontes) if args.horizontes else HORIZONTES)


if __name__ == "__main__":
    raise SystemExit(main())
