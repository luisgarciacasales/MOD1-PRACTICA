"""Etapa F4 — Brief ejecutivo semanal por sector.

Convierte las tablas Gold en la lectura que un comité necesita: dónde cotiza
cada emisora frente a sus pares y frente a su propia historia, qué se movió esta
semana y qué hay en el calendario.

    docker compose exec -T app python -m src.pipeline.brief
    docker compose exec -T app python -m src.pipeline.brief --dry-run
    docker compose exec -T app python -m src.pipeline.brief --sector banca

Es la **única** llamada a un modelo comercial del pipeline. El NLP masivo sigue
en local contra lab-ollama; la política FinOps de CLAUDE.md documenta por qué se
autoriza esta excepción y con qué límites.

Tres cosas que condicionan el diseño:

· **El brief describe, no recomienda.** El backtest de F3 (27-ago-2026) midió
  que las señales de valuación no anticipan retorno futuro. Decir dónde cotiza
  una emisora es un hecho; sugerir que eso indica una oportunidad sería una
  afirmación que los datos del propio proyecto desmienten. La instrucción va en
  el prompt, y el `--dry-run` existe para poder auditarla sin gastar.

· **Solo salen los sectores con comparables.** Con `MIN_EMISORAS_SECTOR = 3`,
  hoy son banca (6) y consumo (3). Un "sector" de una emisora no tiene mediana
  contra la que comparar, y fabricarla sería inventar el producto.

· **El gasto se corta en el código, no en la consola.** El techo del workspace
  es la red de seguridad; si es lo único que hay, un bucle lo agota el día 3 y
  deja al comité sin brief tres semanas. `MAX_LLAMADAS_POR_CORRIDA` aborta
  antes de llegar ahí.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from src.config import get_settings
from src.config.tickers import MIN_EMISORAS_SECTOR
from src.pipeline import db
from src.config.tiempo import hoy_mercado

# Una corrida normal hace UNA llamada. El margen cubre un reintento y un
# segundo sector; cualquier cosa por encima es un bucle, no un brief.
MAX_LLAMADAS_POR_CORRIDA = 3

# Precio de lista de Claude Opus 5, en dólares por millón de tokens. Solo para
# reportar el coste de la corrida — la facturación real la lleva Anthropic.
USD_POR_MTOK_ENTRADA = 5.0
USD_POR_MTOK_SALIDA = 25.0

_SQL_SECTORES = """
WITH ultimo AS (
    SELECT MAX(date) AS d FROM gold_valuation
)
SELECT v.sector, v.ticker, v.date,
       v.adj_close, v.pe_ratio, v.pe_zscore_1y, v.pb_ratio,
       v.pe_premium_sector_pct, v.pe_rank_sector, v.n_sector,
       v.pe_mediana_sector, v.moneda_reporte
FROM gold_valuation v, ultimo
WHERE v.date = ultimo.d
  AND v.n_sector >= %(min_sector)s
  -- El cast es necesario: con el parámetro a NULL, Postgres no puede
  -- inferir su tipo y falla con AmbiguousParameter.
  AND (%(sector)s::text IS NULL OR v.sector = %(sector)s::text)
ORDER BY v.sector, v.pe_rank_sector NULLS LAST
"""

# Retorno de la semana por emisora. Cinco sesiones hábiles hacia atrás desde el
# último cierre, no siete días naturales: el fin de semana no mueve precios.
_SQL_SEMANA = """
WITH sesiones AS (
    SELECT DISTINCT date FROM gold_market_prices ORDER BY date DESC LIMIT 6
),
extremos AS (
    SELECT MIN(date) AS ini, MAX(date) AS fin FROM sesiones
)
SELECT p.ticker,
       100.0 * (f.adj_close / NULLIF(i.adj_close, 0) - 1) AS retorno_semana_pct
FROM (SELECT DISTINCT ticker FROM gold_market_prices) p
JOIN extremos e ON TRUE
JOIN gold_market_prices i ON i.ticker = p.ticker AND i.date = e.ini
JOIN gold_market_prices f ON f.ticker = p.ticker AND f.date = e.fin
"""

# Noticias de la semana ya correlacionadas contra precio. Se piden con su
# retorno del día siguiente para que el brief pueda decir qué se movió DESPUÉS
# de la noticia — sin afirmar que la noticia lo causó.
_SQL_NOTICIAS = """
SELECT DISTINCT ON (g.title, c.ticker)
       c.ticker, g.title, g.sentiment_label, c.news_date,
       c.next_day_return_pct, c.is_proxy, c.original_fintech
FROM gold_news_market_corr c
JOIN gold_enriched_news g ON g.guid = c.news_guid
WHERE c.news_date > CURRENT_DATE - 8
  AND c.ticker = ANY(%(tickers)s)
ORDER BY g.title, c.ticker, c.news_date DESC
"""

_SQL_MACRO = """
SELECT DISTINCT ON (series_id) series_id, series_name, value, date
FROM gold_macro_indicators
WHERE series_id IN ('SF61745', 'SF43718', 'SF43783', 'SP74625')
ORDER BY series_id, date DESC
"""


def recolectar(sector: str | None = None) -> dict:
    """Arma el contexto del brief desde Gold. No llama a ningún modelo."""
    with db.conectar() as cx, cx.cursor() as cur:
        cur.execute(_SQL_SECTORES, {"min_sector": MIN_EMISORAS_SECTOR, "sector": sector})
        filas = cur.fetchall()
        if not filas:
            return {"sectores": {}, "fecha": None}

        cur.execute(_SQL_SEMANA)
        semana = {t: r for t, r in cur.fetchall()}

        tickers = sorted({f[1] for f in filas})
        cur.execute(_SQL_NOTICIAS, {"tickers": tickers})
        noticias = cur.fetchall()

        cur.execute(_SQL_MACRO)
        macro = cur.fetchall()

    sectores: dict[str, dict] = {}
    for (sec, tk, fecha, precio, pe, z, pb, premio, rank, n, mediana, moneda) in filas:
        s = sectores.setdefault(sec, {"emisoras": [], "n": n, "pe_mediana": _r(mediana, 1)})
        s["emisoras"].append({
            "ticker": tk,
            "precio_mxn": _r(precio, 2),
            "pe": _r(pe, 1),
            "pe_zscore_1y": _r(z, 2),
            "pb": _r(pb, 2),
            "premio_vs_mediana_pct": _r(premio, 1),
            "rank": rank,
            "retorno_semana_pct": _r(semana.get(tk), 2),
            # Solo se declara cuando NO es MXN: en el caso normal es ruido.
            **({"moneda_reporte": moneda} if moneda and moneda != "MXN" else {}),
        })

    return {
        "fecha": str(filas[0][2]),
        "sectores": sectores,
        "noticias_semana": [
            {
                "ticker": n[0], "titulo": n[1], "sentimiento": n[2],
                "fecha": str(n[3]), "retorno_dia_siguiente_pct": _r(n[4], 2),
                **({"via_proxy_de": n[6]} if n[5] else {}),
            }
            for n in noticias
        ],
        "macro": [
            {"serie": m[1], "valor": _r(m[2], 2), "fecha": str(m[3])} for m in macro
        ],
    }


def _r(valor, dec: int):
    return None if valor is None else round(float(valor), dec)


SISTEMA = """Eres analista de equity y escribes el brief semanal para el comité de inversión de un fondo mexicano. Cubres emisoras de la BMV, con foco en el sector financiero.

QUÉ SE ESPERA DE TI

El comité ya tiene las tablas. Tu valor es la lectura que la tabla no da: qué cambió, qué destaca y por qué, y qué conviene tener en el radar. Un brief que reordena cifras en prosa no aporta nada.

La lectura más útil es cruzar las dos medidas de valuación, porque dicen cosas distintas:
- z-score de P/U = posición contra su PROPIA historia de un año.
- premio vs. mediana = posición contra sus PARES de hoy.
Cuando divergen, ahí está la información. Z alto con premio bajo significa que se movió el sector entero, no la emisora. Z bajo en TODAS las emisoras de un sector significa que el sector está barato contra su propia historia, que es una lectura distinta de que una emisora lo esté.

LÍMITE QUE NO PUEDES CRUZAR

Describes posición de valuación. NO recomiendas comprar, vender ni mantener, y no insinúas que un múltiplo anticipe un movimiento futuro.

Esto no es prudencia legal: el backtest interno sobre 3,5 años y 16 emisoras midió que estas señales NO predicen exceso de retorno — de hecho, las emisoras caras contra su historia rindieron más que las baratas, de forma monótona a 20 y 60 días. Presentar un múltiplo como señal de entrada contradiría la evidencia del propio sistema. Puedes decir dónde cotiza algo; no puedes sugerir hacia dónde va.

REGLAS DE RIGOR

- Toda cifra que cites viene del contexto. Si no está, no la menciones.
- Si una noticia del contexto no guarda relación real con la emisora a la que se asoció, dilo. El corpus arrastra falsos positivos y señalarlos vale más que integrarlos.
- Una correlación marcada `via_proxy_de` NO es una noticia sobre la emisora: es una fintech sin cotización cuyo impacto se mide sobre un proxy del sector. Trátala como tal.
- Las emisoras con `moneda_reporte` distinta de MXN son matrices extranjeras que cotizan aquí vía SIC: sus múltiplos reflejan al grupo global, no a la filial mexicana. Si las mencionas, acláralo.
- Un movimiento de precio DESPUÉS de una noticia no significa que la noticia lo causara.

FORMA

Español de México, tono sobrio, sin markdown de encabezados. Entre 300 y 400 palabras. Estructura:
1. Un párrafo de apertura con lo más relevante de la semana en una frase, y por qué.
2. Un párrafo por sector con comparables.
3. Cierre de "En el radar": lo que conviene vigilar, con el porqué. Si no tienes base para decir algo útil, dilo en vez de rellenar."""


def redactar(contexto: dict, modelo: str, clave: str) -> tuple[str, dict]:
    """Llama a Claude y devuelve (texto, uso). Falla con mensaje propio."""
    import anthropic

    cliente = anthropic.Anthropic(api_key=clave)
    respuesta = cliente.messages.create(
        model=modelo,
        max_tokens=16000,
        system=SISTEMA,
        messages=[{
            "role": "user",
            "content": (
                "Contexto de la capa Gold (datos reales, cierre más reciente):\n\n"
                f"{json.dumps(contexto, ensure_ascii=False, indent=2)}\n\n"
                "Escribe el brief semanal."
            ),
        }],
    )

    # stop_reason antes que content: en una negativa el content viene vacío o
    # parcial, y leer content[0] sin comprobar rompe con un IndexError que no
    # dice nada sobre lo que pasó.
    if respuesta.stop_reason == "refusal":
        detalle = getattr(respuesta, "stop_details", None)
        motivo = getattr(detalle, "category", None) or "sin categoría"
        raise RuntimeError(f"el modelo declinó la petición (categoría: {motivo})")

    texto = "".join(b.text for b in respuesta.content if b.type == "text")
    uso = {
        "entrada": respuesta.usage.input_tokens,
        "salida": respuesta.usage.output_tokens,
    }
    uso["usd"] = round(
        uso["entrada"] * USD_POR_MTOK_ENTRADA / 1e6
        + uso["salida"] * USD_POR_MTOK_SALIDA / 1e6,
        4,
    )
    return texto.strip(), uso


def _ruta_salida(fecha: date) -> Path:
    # Junto a los datos, no en el repo: es salida de ejecución (invariante 1).
    ruta = Path(get_settings().bronze_path).parent / "briefs"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta / f"{fecha.isoformat()}_sectorial.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.brief",
        description="F4 — brief ejecutivo semanal por sector.",
    )
    parser.add_argument("--sector", default=None,
                        help="Restringe a un sector (por defecto, todos los que tengan comparables).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Arma y muestra el contexto sin llamar al modelo ni gastar.")
    args = parser.parse_args(argv)

    contexto = recolectar(args.sector)
    if not contexto["sectores"]:
        print(
            f"[brief] ningún sector alcanza el mínimo de {MIN_EMISORAS_SECTOR} emisoras "
            "con múltiplo. Sin comparables no hay brief sectorial que escribir.",
            file=sys.stderr,
        )
        return 1

    resumen = " · ".join(f"{s} ({d['n']})" for s, d in contexto["sectores"].items())
    print(f"[brief] cierre {contexto['fecha']} · {resumen} · "
          f"{len(contexto['noticias_semana'])} noticias de la semana")

    if args.dry_run:
        print(json.dumps(contexto, ensure_ascii=False, indent=2))
        print("\n[brief] DRY RUN — no se llamó al modelo, no se gastó nada.")
        return 0

    settings = get_settings()
    clave = settings.clave_anthropic
    if not clave:
        print(
            "[brief] ANTHROPIC_API_KEY no configurada.\n"
            "        La clave se monta como secreto de Docker desde\n"
            "        ~/augmented/secrets/anthropic_api_key en mi-pc (ver compose.yaml).\n"
            "        Usa --dry-run para revisar el contexto sin clave.",
            file=sys.stderr,
        )
        return 1

    # Una corrida = una llamada. El contador existe para que un futuro bucle
    # (reintentos, iteración por sector) no pueda vaciar el techo del workspace
    # y dejar al comité sin brief el resto del mes.
    llamadas = 0
    llamadas += 1
    if llamadas > MAX_LLAMADAS_POR_CORRIDA:
        print(f"[brief] ABORTADO — más de {MAX_LLAMADAS_POR_CORRIDA} llamadas en una "
              "corrida es un bucle, no un brief.", file=sys.stderr)
        return 1

    print(f"[brief] redactando con {settings.anthropic_model_brief}…", flush=True)
    try:
        texto, uso = redactar(contexto, settings.anthropic_model_brief, clave)
    except Exception as exc:  # noqa: BLE001 — el mensaje importa más que el tipo
        print(f"[brief] FALLÓ: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    destino = _ruta_salida(date.fromisoformat(contexto["fecha"]))
    destino.write_text(
        f"# Brief sectorial — {contexto['fecha']}\n\n{texto}\n\n"
        f"---\nGenerado el {hoy_mercado().isoformat()} con "
        f"{settings.anthropic_model_brief} sobre datos de gold_valuation, "
        f"gold_market_prices y gold_news_market_corr.\n"
        f"Describe posición de valuación; no constituye recomendación de inversión "
        f"(ver el backtest de F3 en gold_backtest_senal).\n",
        encoding="utf-8",
    )

    print()
    print(texto)
    print()
    print(f"[brief] {uso['entrada']} tokens de entrada · {uso['salida']} de salida "
          f"· ~${uso['usd']} a precio de lista")
    print(f"[brief] escrito en {destino}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
