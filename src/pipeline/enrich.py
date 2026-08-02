"""Etapa 4 — Enriquecimiento NLP (Silver → Gold). PRD §4.4 paso 4, §6.4.

Toma `silver_news` con `enriched = false` y las manda al `lab-ollama`
compartido en **lotes de 8 llamadas simultáneas** (`asyncio` + `aiohttp`), que
es la optimización central del PRD §2.1: satura la GPU sin desbordar los 16 GB.

Dos pasadas por noticia, según el PRD:
  1. NER (tickers, personas, organizaciones, sectores) + sentimiento.
  2. Detección M&A + etiquetado fintech + sector afectado para el proxy.

Todo lo que devuelve el modelo se **valida contra vocabularios cerrados** antes
de escribirse: en las pruebas iniciales inventó el ticker "BANR" para Banorte.
Un ticker inventado no rompe nada visiblemente — simplemente no hace JOIN con
gold_market_prices, y la noticia desaparece del análisis en silencio.

    docker compose exec -T app python -m src.pipeline.enrich
    docker compose exec -T app python -m src.pipeline.enrich --limit 10
    docker compose exec -T app python -m src.pipeline.enrich --reprocess
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.config import get_settings
from src.config.emisoras import ALIAS_EMISORAS
from src.config.tickers import SECTORES_VALIDOS
from src.pipeline import db
from src.pipeline.ollama import ClienteOllama, salud
from src.prompts import (
    LIMITE_CUERPO_PROMPT,
    SISTEMA_MA,
    SISTEMA_NER,
    USUARIO_MA,
    USUARIO_NER,
)

TICKERS_VALIDOS = frozenset(ALIAS_EMISORAS)
SENTIMIENTOS = {"positive", "negative", "neutral"}
TIPOS_MA = {"acquisition", "merger", "partnership", "none"}


@dataclass
class Enriquecida:
    guid: str
    ner_tickers: list[str] = field(default_factory=list)
    ner_persons: list[str] = field(default_factory=list)
    ner_orgs: list[str] = field(default_factory=list)
    ner_sectors: list[str] = field(default_factory=list)
    sentiment_score: float | None = None
    sentiment_label: str | None = None
    is_ma_event: bool = False
    ma_event_type: str = "none"
    ma_confidence: float | None = None
    fintech_flag: bool = False
    fintechs_identified: list[str] = field(default_factory=list)
    traditional_banks_mentioned: list[str] = field(default_factory=list)
    sector_affected: str | None = None
    errores: list[str] = field(default_factory=list)


# --- Saneamiento de la salida del modelo -----------------------------------


def _lista_de_texto(valor: Any, *, limite: int = 32) -> list[str]:
    if not isinstance(valor, list):
        return []
    limpios: list[str] = []
    for x in valor:
        if isinstance(x, str) and x.strip():
            limpios.append(x.strip()[:128])
    # dict.fromkeys deduplica preservando orden.
    return list(dict.fromkeys(limpios))[:limite]


def _numero_0_1(valor: Any) -> float | None:
    try:
        n = float(valor)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return min(max(n, 0.0), 1.0)


def aplicar_ner(destino: Enriquecida, datos: dict[str, Any], fintechs: set[str]) -> None:
    """Filtra la salida del modelo contra los vocabularios cerrados."""
    # Los tickers se cotejan contra el universo real: el modelo alucina
    # símbolos y uno inventado no falla, solo deja de hacer JOIN.
    destino.ner_tickers = [
        t for t in _lista_de_texto(datos.get("tickers")) if t in TICKERS_VALIDOS
    ]
    destino.ner_persons = _lista_de_texto(datos.get("personas"))
    destino.ner_orgs = _lista_de_texto(datos.get("empresas"))
    destino.ner_sectors = [
        s for s in _lista_de_texto(datos.get("sectores")) if s in SECTORES_VALIDOS
    ]

    etiqueta = str(datos.get("sentimiento", "")).strip().lower()
    destino.sentiment_label = etiqueta if etiqueta in SENTIMIENTOS else "neutral"
    destino.sentiment_score = _numero_0_1(datos.get("confianza_sentimiento"))


def aplicar_ma(destino: Enriquecida, datos: dict[str, Any], fintechs: set[str]) -> None:
    es_ma = bool(datos.get("es_evento_ma"))
    tipo = str(datos.get("tipo_ma", "none")).strip().lower()
    tipo = tipo if tipo in TIPOS_MA else "none"

    # El CHECK de la tabla exige coherencia; se impone aquí para que el fallo
    # no llegue como una violación de restricción a mitad del lote.
    if not es_ma or tipo == "none":
        es_ma, tipo = False, "none"

    destino.is_ma_event = es_ma
    destino.ma_event_type = tipo
    destino.ma_confidence = _numero_0_1(datos.get("confianza_ma"))

    detectadas = [f for f in _lista_de_texto(datos.get("fintechs")) if f in fintechs]
    destino.fintechs_identified = detectadas
    # La bandera se deriva de lo verificado, no de lo que el modelo afirme:
    # decía "menciona_fintech: true" citando fintechs que no existen.
    destino.fintech_flag = bool(detectadas)
    destino.traditional_banks_mentioned = _lista_de_texto(
        datos.get("bancos_tradicionales")
    )

    sector = datos.get("sector_afectado")
    sector = str(sector).strip() if isinstance(sector, str) else ""
    destino.sector_affected = sector if sector in SECTORES_VALIDOS else None


# --- Inferencia -------------------------------------------------------------


async def enriquecer_una(
    cliente: ClienteOllama,
    fila: dict[str, Any],
    *,
    modelo_ner: str,
    modelo_ma: str,
    fintechs: set[str],
    catalogos: dict[str, str],
) -> Enriquecida:
    """Las dos pasadas de una noticia. Nunca lanza: un fallo se anota."""
    resultado = Enriquecida(guid=fila["guid"])
    cuerpo = (fila["content"] or "")[:LIMITE_CUERPO_PROMPT]
    comun = {"titulo": fila["title"], "cuerpo": cuerpo, **catalogos}

    # Las dos pasadas de una misma noticia van en paralelo: son independientes
    # y así el semáforo se llena con trabajo útil en lugar de esperar.
    r_ner, r_ma = await asyncio.gather(
        cliente.chat_json(
            modelo=modelo_ner, sistema=SISTEMA_NER, usuario=USUARIO_NER.format(**comun)
        ),
        cliente.chat_json(
            modelo=modelo_ma, sistema=SISTEMA_MA, usuario=USUARIO_MA.format(**comun)
        ),
    )

    if r_ner.ok and r_ner.datos is not None:
        aplicar_ner(resultado, r_ner.datos, fintechs)
    else:
        resultado.errores.append(f"NER: {r_ner.error}")

    if r_ma.ok and r_ma.datos is not None:
        aplicar_ma(resultado, r_ma.datos, fintechs)
    else:
        resultado.errores.append(f"MA: {r_ma.error}")

    return resultado


_SQL_PENDIENTES = """
SELECT guid, source, title, content, url, published_at, ingested_at
FROM silver_news
WHERE (%(reprocesar)s OR NOT enriched)
ORDER BY published_at DESC
LIMIT %(limite)s
"""

_SQL_UPSERT_GOLD = """
INSERT INTO gold_enriched_news (
    guid, source, title, content, url, published_at, ingested_at,
    ner_tickers, ner_persons, ner_orgs, ner_sectors,
    sentiment_score, sentiment_label,
    is_ma_event, ma_event_type, ma_confidence,
    fintech_flag, fintechs_identified, traditional_banks_mentioned, sector_affected,
    enriched_at, model_version
) VALUES (
    %(guid)s, %(source)s, %(title)s, %(content)s, %(url)s, %(published_at)s, %(ingested_at)s,
    %(ner_tickers)s, %(ner_persons)s, %(ner_orgs)s, %(ner_sectors)s,
    %(sentiment_score)s, %(sentiment_label)s,
    %(is_ma_event)s, %(ma_event_type)s, %(ma_confidence)s,
    %(fintech_flag)s, %(fintechs_identified)s, %(traditional_banks_mentioned)s, %(sector_affected)s,
    NOW(), %(model_version)s
)
ON CONFLICT (guid) DO UPDATE SET
    ner_tickers = EXCLUDED.ner_tickers, ner_persons = EXCLUDED.ner_persons,
    ner_orgs = EXCLUDED.ner_orgs, ner_sectors = EXCLUDED.ner_sectors,
    sentiment_score = EXCLUDED.sentiment_score, sentiment_label = EXCLUDED.sentiment_label,
    is_ma_event = EXCLUDED.is_ma_event, ma_event_type = EXCLUDED.ma_event_type,
    ma_confidence = EXCLUDED.ma_confidence,
    fintech_flag = EXCLUDED.fintech_flag, fintechs_identified = EXCLUDED.fintechs_identified,
    traditional_banks_mentioned = EXCLUDED.traditional_banks_mentioned,
    sector_affected = EXCLUDED.sector_affected,
    enriched_at = NOW(), model_version = EXCLUDED.model_version
    -- `embedding` NO se toca: lo escribe `index` y una reinferencia del LLM no
    -- tiene por qué invalidar un vector ya calculado.
RETURNING (xmax = 0)
"""


async def ejecutar(*, limite: int, reprocesar: bool) -> int:
    settings = get_settings()

    vivo, detalle = await salud()
    if not vivo:
        print(f"[enrich] lab-ollama no responde en {settings.ollama_base_url}: {detalle}",
              file=sys.stderr)
        return 1
    print(f"[enrich] lab-ollama OK ({detalle})")

    with db.conectar() as conexion, conexion.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(_SQL_PENDIENTES, {"reprocesar": reprocesar, "limite": limite})
        pendientes = cur.fetchall()
        cur.execute("SELECT commercial_name FROM silver_fintech_dict")
        fintechs = {f["commercial_name"] for f in cur.fetchall()}

    if not pendientes:
        print("[enrich] no hay noticias pendientes de enriquecer")
        return 0

    catalogos = {
        "tickers_permitidos": "\n".join(f"- {t}" for t in sorted(TICKERS_VALIDOS)),
        "sectores_permitidos": "\n".join(f"- {s}" for s in sorted(SECTORES_VALIDOS)),
        "fintechs_permitidas": "\n".join(f"- {f}" for f in sorted(fintechs)) or "- (ninguna)",
    }

    print(
        f"[enrich] {len(pendientes)} noticias · lotes de {settings.nlp_batch_size} "
        f"· NER={settings.ollama_model_ner} · M&A={settings.ollama_model_ma}"
    )

    inicio = time.monotonic()
    async with ClienteOllama(concurrencia=settings.nlp_batch_size) as cliente:
        resultados = await asyncio.gather(
            *(
                enriquecer_una(
                    cliente,
                    fila,
                    modelo_ner=settings.ollama_model_ner,
                    modelo_ma=settings.ollama_model_ma,
                    fintechs=fintechs,
                    catalogos=catalogos,
                )
                for fila in pendientes
            )
        )
    transcurrido = time.monotonic() - inicio

    # --- Persistencia --------------------------------------------------------
    modelo = f"{settings.ollama_model_ner}+{settings.ollama_model_ma}"
    nuevas = actualizadas = fallidas = 0

    with db.conectar() as conexion, conexion.cursor() as cur:
        for fila, res in zip(pendientes, resultados, strict=True):
            if len(res.errores) == 2:
                # Las dos pasadas fallaron: no hay nada que guardar y la
                # noticia debe seguir pendiente para el próximo intento.
                fallidas += 1
                continue

            cur.execute(_SQL_UPSERT_GOLD, {
                "guid": res.guid, "source": fila["source"], "title": fila["title"],
                "content": fila["content"], "url": fila["url"],
                "published_at": fila["published_at"], "ingested_at": fila["ingested_at"],
                "ner_tickers": res.ner_tickers, "ner_persons": res.ner_persons,
                "ner_orgs": res.ner_orgs, "ner_sectors": res.ner_sectors,
                "sentiment_score": res.sentiment_score, "sentiment_label": res.sentiment_label,
                "is_ma_event": res.is_ma_event, "ma_event_type": res.ma_event_type,
                "ma_confidence": res.ma_confidence,
                "fintech_flag": res.fintech_flag,
                "fintechs_identified": res.fintechs_identified,
                "traditional_banks_mentioned": res.traditional_banks_mentioned,
                "sector_affected": res.sector_affected,
                "model_version": modelo,
            })
            if cur.fetchone()[0]:
                nuevas += 1
            else:
                actualizadas += 1

            # La marca se pone SOLO tras persistir en Gold: si el proceso muere
            # a mitad, las no escritas siguen pendientes y se reintentan.
            cur.execute("UPDATE silver_news SET enriched = TRUE WHERE guid = %s", (res.guid,))
        conexion.commit()

    # --- Resumen -------------------------------------------------------------
    parciales = sum(1 for r in resultados if len(r.errores) == 1)
    print()
    print(f"{'enriquecidas nuevas':<26} {nuevas}")
    print(f"{'actualizadas':<26} {actualizadas}")
    print(f"{'con una pasada fallida':<26} {parciales}")
    print(f"{'fallidas por completo':<26} {fallidas}")
    print(f"{'tiempo':<26} {transcurrido:.1f} s "
          f"({transcurrido / max(len(pendientes), 1):.2f} s/noticia)")

    if fallidas:
        muestra = next((r.errores[0] for r in resultados if len(r.errores) == 2), "")
        print(f"\nejemplo de fallo: {muestra[:200]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="src.pipeline.enrich",
        description="Enriquecimiento NLP local con Ollama, async en lotes.",
    )
    parser.add_argument("--limit", type=int, default=1000,
                        help="Máximo de noticias a procesar.")
    parser.add_argument("--reprocess", action="store_true",
                        help="Reprocesa también las ya enriquecidas.")
    parser.add_argument("--batch-size", type=int, default=None,
                        help="Llamadas simultáneas (por defecto NLP_BATCH_SIZE=8).")
    args = parser.parse_args(argv)

    if args.batch_size:
        # Se inyecta en settings para que el cliente y el resumen coincidan.
        get_settings().nlp_batch_size = args.batch_size

    return asyncio.run(ejecutar(limite=args.limit, reprocesar=args.reprocess))


if __name__ == "__main__":
    raise SystemExit(main())
