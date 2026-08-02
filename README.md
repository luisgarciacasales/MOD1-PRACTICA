# MOD1-PRACTICA — Analítica Avanzada de Equity BMV

Motor de analítica NLP + mercado sobre **arquitectura Medallón** (Bronze → Silver
→ Gold → FAISS) que relaciona eventos corporativos extraídos de texto no
estructurado con el desempeño bursátil de emisoras de la Bolsa Mexicana de
Valores, con foco en oportunidades de M&A y en la competencia entre banca
tradicional y fintechs.

Todo corre **local**: PostgreSQL + pgvector en Docker y un LLM cuantizado en
Ollama sobre una RTX 5080. Cero llamadas a APIs comerciales de LLM.

| | |
|---|---|
| **Requisitos de producto** | [`docs/PRD.md`](docs/PRD.md) |
| **Arquitectura y decisiones** | [`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md) |
| **Evidencia de ejecución** | [`docs/EVIDENCIA_E2E.md`](docs/EVIDENCIA_E2E.md) |
| **Operación desde el Mac** | [`README_MAC.md`](README_MAC.md) |
| **Arnés de agentes** | [`docs/HARNESS.md`](docs/HARNESS.md) · [`CLAUDE.md`](CLAUDE.md) |

## Flujo implementado

```
        5 FUENTES                    BRONZE                      SILVER
 ┌────────────────────┐      ┌───────────────────┐      ┌──────────────────────┐
 │ BMV Eventos     ✗  │      │ JSON + Parquet    │      │ Contrato Pydantic    │
 │ El Financiero   ✓  │─────▶│ + metadata.json   │─────▶│  · tipado estricto   │
 │ El Economista   ✗  │      │ (batch_uuid,      │      │  · ≥1 Ticker/Sector/ │
 │ Bloomberg Línea ✓  │      │  SHA-256, ts)     │      │    Entidad           │
 │ Finnovista      ✓  │      │                   │      │  · bypass macro      │
 │ Yahoo Finance   ✓  │      │ inmutable (0444)  │      │                      │
 │ BANXICO SIE     ✓  │      │ sin transformar   │      │ FAIL → dead_letters  │
 └────────────────────┘      └───────────────────┘      │ OK   → UPSERT        │
                                                        └──────────┬───────────┘
                                                                   │
        GOLD                                                       ▼
 ┌──────────────────────────────────────────────────────────────────────────┐
 │  enrich      Ollama qwen3.5:9b · async ×8 · NER + sentimiento + M&A       │
 │  transform   retornos, medias móviles 7/30d, volatilidad, YoY             │
 │  correlate   JOIN temporal con calendario XMEX · directo y proxy ticker   │
 │  index       embeddings e5-large (1024d) → FAISS IndexFlatIP → coseno     │
 └──────────────────────────────────────────────────────────────────────────┘
```

`✗` = fuente configurada pero inaccesible hoy; la ingesta es *fail-soft* por
fuente y el resto del lote continúa. El detalle está en
[`docs/ARQUITECTURA.md`](docs/ARQUITECTURA.md#fuentes-estado-real).

## Puesta en marcha

Requisitos: Docker con Compose v2 y un Ollama accesible en `:11434` con un
modelo instruct de 7–9B cuantizado a 4 bits.

```bash
git clone <repo> MOD1-PRACTICA && cd MOD1-PRACTICA

make init                       # crea data/ con el ownership correcto
cp .env.example .env && $EDITOR .env    # POSTGRES_PASSWORD y BANXICO_TOKEN
make up                         # levanta postgres (pgvector) + app
make migrate                    # aplica el esquema Silver/Gold
```

`make init` **antes** del primer `up` no es opcional: si `data/` no existe,
Docker crea el bind mount como `root` y el contenedor —que corre con tu UID— no
puede escribir en Bronze.

El `.env` se crea **a mano** y nunca entra al repositorio. `BANXICO_TOKEN` es
gratuito y se obtiene en <https://www.banxico.org.mx/SieAPIRest/>; sin él la
ingesta macro falla en soft y las otras cuatro fuentes continúan.

## Ejecutar el pipeline

```bash
make ingest       # 5 fuentes → Bronze (inmutable)
make validate     # Bronze → Silver, con cuarentena
make enrich       # NER, sentimiento, M&A y fintech con el LLM local
make transform    # métricas derivadas de mercado y macro
make correlate    # JOIN temporal noticias ↔ precios (XMEX)
make index        # embeddings + índice FAISS
```

Cada etapa es idempotente: reprocesar el mismo lote produce `filas_nuevas = 0`.

## Consultar la capa Gold

```bash
make search Q="recorte de la tasa de interés de Banxico y su efecto en la banca"
```

```
 1. [0.832] Ganancias de CFE se desploman por el dólar débil
     bloomberg · 2026-07-30 · negative
 2. [0.829] ¿Invertir el día de la reunión de la Fed? Así suelen reaccionar…
     bloomberg · 2026-07-29 · neutral
```

Desde Python: `search_semantic(query, top_k)` y `get_market_context(ticker)` en
[`src/pipeline/search.py`](src/pipeline/search.py).

## Reproducir la evidencia

```bash
make demo ARGS=--desde-cero
```

Vacía Silver y Gold —**nunca Bronze**, que es inmutable y basta para
reconstruir el resto—, ejecuta la cadena completa **dos veces sobre el mismo
lote** y escribe un informe con las tablas de los cinco criterios de
aceptación. Salida real en [`docs/EVIDENCIA_E2E.md`](docs/EVIDENCIA_E2E.md).

| Criterio | Resultado |
|---|---|
| Bronze — 2+ lotes crudos, timestamp, sin transformar | 18 lotes, checksum SHA-256 íntegro, modo `0444` |
| Contrato — validación explícita, cuarentena con motivo | 1 116 registros en cuarentena, motivos tipados |
| Idempotencia — reproceso da `filas_nuevas = 0` | pasada 1: 12 259 filas · pasada 2: **0** |
| Duplicados — `COUNT(*) > 1` por clave natural | **0 grupos** en 7 tablas |
| Gold — índice vectorial, 1+ consulta semántica | 119 vectores de 1024d, 3 consultas en 78–87 ms |

Los criterios ampliados del PRD §8 se comprueban aparte:

```bash
make verify       # 17 checks · PASS/FAIL con su evidencia
make test         # 130 pruebas unitarias
```

## Estructura

```
compose.yaml            app + postgres(pgvector); reusa un Ollama externo
docker/Dockerfile       imagen de la app (patrón UID/GID del host)
sql/                    migraciones idempotentes de Silver y Gold
seed/                   diccionario Finnovista (dato de referencia versionado)
scripts/demo_e2e.py     demostración de punta a punta + tablas de evidencia
src/
├── config/             fuentes, tickers, sector→proxy, series BANXICO, settings
├── contracts/          modelos Pydantic de Silver + cola de cuarentena
├── sources/            adaptadores de las 5 fuentes (fail-soft)
└── pipeline/           las 8 etapas + cliente Ollama, calendario y búsqueda
tests/                  130 pruebas
```

## Límites conocidos

Honestidad por delante: el sistema está completo, **el corpus no**.

- **Dos fuentes de noticias no son accesibles.** El Economista responde 403 al
  detectar una IP de datacenter y la página de Eventos Relevantes de la BMV es
  una SPA sin endpoint público. La ingesta las tolera en soft; el efecto es que
  hay poca noticia mexicana sobre emisoras concretas.
- **Los criterios se cumplen con un caso de cada cosa**, no con volumen: 1
  evento M&A, 1 correlación proxy, 5 correlaciones en total. La maquinaria está
  verificada; alimentarla mejor es cuestión de fuentes, no de código.
- **El paralelismo del enriquecimiento no acelera** mientras el Ollama
  compartido corra con `OLLAMA_NUM_PARALLEL=1`. Ver
  [ADR-13](docs/ARQUITECTURA.md#decisiones).

## Licencia y alcance

Trabajo académico (AI Engineering Lab, Fase 1). Fuera de alcance en esta fase:
GCP, Neo4j/GraphRAG, LLMs comerciales y Terraform.
