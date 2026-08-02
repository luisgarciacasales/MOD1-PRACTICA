---
name: medallion-pipeline
description: Operar y razonar las etapas de la arquitectura Medallón Bronze→Silver→Gold→FAISS con la semántica del PRD (ingesta batch fail-soft, cargas idempotentes UPSERT, enriquecimiento NLP async×8 con Ollama, calendario XMEX y proxy ticker de fintechs). Úsalo para ejecutar, depurar o extender el pipeline de datos.
---

# medallion-pipeline — Bronze→Silver→Gold→FAISS

Traduce el flujo del PRD (§4.4, §6) a operaciones concretas. Es la referencia de **cómo debe comportarse** cada etapa; la ejecución real ocurre dentro de contenedores en `mi-pc` (usa **remote-ops**/**deploy** para invocarla).

## Mapa de etapas

| Etapa | Entrada → Salida | Invariante clave |
| --- | --- | --- |
| **Ingesta** | 5 fuentes → `data/bronze/{news,market}/{source}/{YYYY-MM-DD}/{batch_id}/` | Inmutable, **sin transformar**; JSON + Parquet + `metadata.json` (batch_uuid, source, checksum SHA-256). Fail-soft por fuente. |
| **Validación** | Bronze → `silver_*` / `silver_dead_letters` | Contrato Pydantic (tipos + ≥1 Ticker/Sector/Entidad). Ver skill **data-contracts**. |
| **Carga** | Silver → PostgreSQL | `INSERT ... ON CONFLICT (<clave natural>) DO UPDATE`. Reproceso ⇒ `filas_nuevas = 0`. |
| **Enriquecimiento NLP** | `silver_news (enriched=false)` → `gold_enriched_news` | Ollama async, **lotes de 8**. NER + sentimiento + M&A + fintech tag + proxy. |
| **Transformación mercado** | `silver_market_prices/macro` → `gold_*` | Retornos, medias móviles 7/30d, volatilidad 30d, YoY. |
| **Correlación** | `gold_enriched_news` ⋈ `gold_market_prices` → `gold_news_market_corr` | JOIN temporal con **XMEX** (siguiente día hábil). Directo o **proxy**. |
| **Indexación** | embeddings → FAISS `.index` (NVMe) | `IndexFlatIP`, coseno, Top-K. Incremental. |

## Las 5 fuentes

Noticias (texto): BMV Eventos Relevantes (scraping ligero), El Financiero, El Economista, Bloomberg Línea MX (RSS). Mercado (estructurado): Yahoo Finance `yfinance` (tickers `.MX`), BANXICO/SIE (series `SF...`). Toda llamada a `yfinance`/BANXICO pasa por **`requests-cache` (SQLite)** — TTL diario para mercado, semanal para macro mensual.

## Reglas semánticas que no se pueden romper

- **Bronze inmutable.** Ninguna limpieza en Bronze. Si necesitas corregir, se hace aguas abajo, nunca reescribiendo Bronze.
- **Idempotencia.** Toda carga a Silver/Gold usa clave natural: `guid` (noticias, SHA-256 de `source+url+published_at`), `(ticker, date)` (precios), `(series_id, date)` (macro). Reprocesar el mismo batch **no** crea filas nuevas.
- **Fail-soft.** Si una fuente falla, las demás continúan; registra el fallo, no abortes el batch.
- **Async×8.** El enriquecimiento envía lotes de 8 noticias simultáneas a Ollama (`asyncio` + `aiohttp`). Escalar a 16 solo tras verificar VRAM (`nvidia-smi`, ver remote-ops). Ollama = `http://host.docker.internal:11434`.
- **Calendario XMEX.** El JOIN noticias↔precios usa `pandas_market_calendars` (XMEX): noticia de viernes → impacto medido el **siguiente día hábil** (lunes o el próximo si es feriado). Nunca fecha calendario.
- **Proxy ticker.** Fintech sin cotización BMV (Nu, Ualá, Stori, Klar) → el LLM infiere el sector afectado → mapea a ticker proxy (tabla §3.3 del PRD: banca_consumo→GFNORTE.MX/BBAJIO.MX, etc.). Marca `is_proxy = true`, guarda `original_fintech`, `sector_affected`, `proxy_ticker`.
- **Bypass macro.** Noticias macro (tasas/inflación/política monetaria) sin ticker pueden pasar a `silver_news` con `macro_bypass = true`. El léxico macro es **obligatorio**; `source=bloomberg` solo baja el umbral, no lo sustituye (ADR-10). Ver **data-contracts**.

## Ejecución (dentro de contenedores en mi-pc)

Los comandos concretos dependen del scaffold (aún no creado). Patrón esperado:
```bash
# vía remote-ops / deploy:
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m pipeline.ingest --date <YYYY-MM-DD>'
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m pipeline.validate --date <YYYY-MM-DD>'
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m pipeline.enrich --batch-size 8'
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m pipeline.correlate --date <YYYY-MM-DD>'
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m pipeline.index'
```
> Cuando definas estos módulos, mantén los nombres/flags estables: los skills **acceptance-verify** y las docs los referencian.

## SLAs objetivo (§7 del PRD)

Ingesta 5 fuentes < 15 min · Validación ≤500 noticias < 1 min · NLP async×8 ≤500 < 10 min · Indexación < 30 s · Búsqueda Top-K < 500 ms · Correlación < 5 s · Gold disponible < 30 min post-cierre.

## Verificación

Tras operar, corre el skill **acceptance-verify** para confirmar los umbrales de la Definición de Terminado (idempotencia, dead-letters, proxy, XMEX, FAISS).
