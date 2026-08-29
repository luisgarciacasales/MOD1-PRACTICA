"""Vigilancia continua de calidad de datos.

Deliberadamente **separado de `verify`**. Aquellos 17 checks son la Definición
de Terminado del PRD §8 — responden «¿está construido lo que se prometió?». Los
de aquí responden otra pregunta: «¿los datos que hay dentro son creíbles?». Un
dato sospechoso de una fuente externa no significa que el sistema esté sin
terminar, y mezclarlos confundiría las dos cosas.

    docker compose exec -T app python -m src.pipeline.calidad

**Cada check de este módulo nace de un defecto real que estuvo en producción
sin que nadie lo viera**, y esa es la razón de que exista: los cuatro se
encontraron por casualidad mientras se investigaba otra cosa. El objetivo es que
el siguiente no dependa de la suerte.

Severidades, y por qué no todo es un fallo:

· `PROBLEMA` — dato demostrablemente incorrecto. Sale con código 1.
· `SOSPECHA` — patrón estadísticamente improbable que merece una mirada humana.
  No es un fallo: puede ser legítimo. Sale con código 0.
· `OK` — sin señales.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from src.pipeline import db

OK, SOSPECHA, PROBLEMA = "OK", "SOSPECHA", "PROBLEMA"


@dataclass
class Senal:
    aspecto: str
    estado: str
    evidencia: str


def _filas(cur, sql: str, params: tuple = ()) -> list:
    cur.execute(sql, params)
    return cur.fetchall()


def check_upa_redondeada(cur) -> Senal:
    """UPA con valor entero exacto: huella de una fuente que redondea.

    Encontrado el 28-ago-2026 comparando los PDF de Banorte con Yahoo: 8 de 65
    UPA trimestrales eran enteros exactos (12,3%) frente a 0 de 64 en la serie
    anual, y las ocho del primer trimestre de cuatro emisoras financieras.
    GFNORTEO figuraba con 5,0000 donde su reporte oficial dice 5,435 — un 8% de
    error que entra en el TTM y de ahí al P/U.

    Con dos decimales, el azar daría menos de un caso por cada cien valores. Un
    porcentaje de un dígito alto o más es la fuente redondeando, no la
    casualidad.
    """
    filas = _filas(cur, """
        SELECT ticker, period_end::text, utilidad_por_accion
        FROM silver_fundamentals
        WHERE utilidad_por_accion IS NOT NULL
          AND utilidad_por_accion = ROUND(utilidad_por_accion::numeric)
        ORDER BY ticker, period_end
    """)
    total = _filas(cur, """
        SELECT COUNT(*) FROM silver_fundamentals WHERE utilidad_por_accion IS NOT NULL
    """)[0][0]

    if not filas:
        return Senal("UPA redondeada a entero", OK, f"0 de {total} valores")

    pct = 100.0 * len(filas) / total if total else 0
    muestra = ", ".join(f"{t} {p[:7]}={v:g}" for t, p, v in filas[:3])
    estado = PROBLEMA if pct >= 5 else SOSPECHA
    return Senal(
        "UPA redondeada a entero", estado,
        f"{len(filas)} de {total} ({pct:.1f}%) · {muestra}"
        + (f" · +{len(filas) - 3} más" if len(filas) > 3 else ""),
    )


def check_huecos_trimestrales(cur) -> Senal:
    """Trimestres ausentes dentro del rango cubierto de cada emisora.

    Un hueco no es un dato faltante inocuo: `eps_ttm` exige cuatro trimestres
    consecutivos, así que un trimestre perdido borra CUATRO puntos de valuación,
    no uno. Se vio con `3T24.pdf` de Banorte, que no se pudo extraer.

    Solo cuenta los huecos INTERIORES: que una serie empiece en 2022 no es un
    hueco, es su comienzo.
    """
    filas = _filas(cur, """
        WITH rangos AS (
            SELECT ticker, MIN(period_end) AS ini, MAX(period_end) AS fin
            FROM silver_fundamentals GROUP BY ticker
        ),
        esperados AS (
            SELECT r.ticker, generate_series(r.ini, r.fin, INTERVAL '3 months')::date AS perio
            FROM rangos r
        )
        SELECT e.ticker, COUNT(*) AS huecos
        FROM esperados e
        LEFT JOIN silver_fundamentals f
               ON f.ticker = e.ticker AND f.period_end = e.perio
        WHERE f.period_end IS NULL
        GROUP BY e.ticker ORDER BY 2 DESC
    """)
    if not filas:
        return Senal("Huecos en la serie trimestral", OK, "ninguna emisora con huecos interiores")
    detalle = ", ".join(f"{t}: {n}" for t, n in filas[:4])
    return Senal(
        "Huecos en la serie trimestral", SOSPECHA,
        f"{sum(n for _, n in filas)} trimestres ausentes · {detalle}"
        " · cada hueco borra 4 puntos de valuación (eps_ttm exige 4 consecutivos)",
    )


def check_precios_congelados(cur) -> Senal:
    """Filas de precio que ningún refresco vuelve a tocar.

    El refresco semanal cubre 10 años, o toda la serie en las emisoras de
    `TICKERS_HISTORIA_COMPLETA`. Lo que queda fuera nunca se re-ajusta, así que
    un split o dividendo reescalaría `adj_close` solo en el tramo reciente y
    dejaría una discontinuidad invisible justo en el borde.

    GFNORTEO queda excluida a propósito: su refresco es `max`, así que su
    historia profunda SÍ se re-ajusta.
    """
    from src.config.tickers import TICKERS_HISTORIA_COMPLETA, VENTANA_HISTORICA_ANIOS

    filas = _filas(cur, """
        SELECT ticker, COUNT(*)
        FROM silver_market_prices
        WHERE date < CURRENT_DATE - (%s * 365)
          AND ticker <> ALL(%s)
        GROUP BY ticker ORDER BY 2 DESC
    """, (VENTANA_HISTORICA_ANIOS, list(TICKERS_HISTORIA_COMPLETA)))

    if not filas:
        return Senal("Precios fuera del refresco", OK,
                     f"ninguno · ventana {VENTANA_HISTORICA_ANIOS} años")
    total = sum(n for _, n in filas)
    return Senal(
        "Precios fuera del refresco", SOSPECHA,
        f"{total} filas en {len(filas)} emisoras nunca se re-ajustan "
        f"(ej. {filas[0][0]}: {filas[0][1]}) · un split dejaría discontinuidad",
    )


def check_correlaciones_sin_ner(cur) -> Senal:
    """Correlaciones directas que el NER no respalda.

    Desde ADR-17 el veredicto del NER manda sobre el del léxico, así que esto
    debe ser cero. Si deja de serlo, alguien puso `CONFIAR_EN_NER = False` o
    `correlate` no se ha vuelto a ejecutar tras un reproceso del NER — y en
    ambos casos vuelve el ruido que ese ADR quitó.
    """
    n = _filas(cur, """
        SELECT COUNT(*)
        FROM gold_news_market_corr c
        JOIN gold_enriched_news g ON g.guid = c.news_guid
        WHERE NOT c.is_proxy
          AND g.ner_tickers IS NOT NULL
          AND NOT (c.ticker = ANY(g.ner_tickers))
    """)[0][0]
    if n == 0:
        return Senal("Correlaciones sin respaldo del NER", OK, "0 · coherente con ADR-17")
    return Senal(
        "Correlaciones sin respaldo del NER", PROBLEMA,
        f"{n} correlaciones que ADR-17 debería haber retirado · "
        "¿CONFIAR_EN_NER en False, o falta reejecutar correlate?",
    )


CHECKS = (
    check_upa_redondeada,
    check_huecos_trimestrales,
    check_precios_congelados,
    check_correlaciones_sin_ner,
)


def main(argv: list[str] | None = None) -> int:
    señales: list[Senal] = []
    with db.conectar() as cx, cx.cursor() as cur:
        for check in CHECKS:
            try:
                señales.append(check(cur))
            except Exception as exc:  # noqa: BLE001
                señales.append(Senal(check.__name__, PROBLEMA,
                                     f"el check falló: {type(exc).__name__}: {exc}"))

    print("Calidad de los datos — vigilancia continua")
    print("=" * 100)
    print(f"{'ASPECTO':<36}{'ESTADO':<11}EVIDENCIA")
    print("-" * 100)
    for s in señales:
        print(f"{s.aspecto:<36}{s.estado:<11}{s.evidencia}")
    print("-" * 100)

    problemas = sum(s.estado == PROBLEMA for s in señales)
    sospechas = sum(s.estado == SOSPECHA for s in señales)
    print(f"{sum(s.estado == OK for s in señales)} OK · {sospechas} SOSPECHA · "
          f"{problemas} PROBLEMA")
    if sospechas and not problemas:
        print("\nUna SOSPECHA no es un fallo: es un patrón que merece una mirada "
              "humana y puede ser legítimo.")
    return 1 if problemas else 0


if __name__ == "__main__":
    raise SystemExit(main())
