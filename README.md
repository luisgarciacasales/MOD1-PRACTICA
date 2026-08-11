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
| **Corrida diaria (paso a paso)** | [`docs/RUNBOOK.md`](docs/RUNBOOK.md) |
| **Operación en dos máquinas** | [`README_MAC.md`](README_MAC.md) |
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

### Requisitos

| | Mínimo | Notas |
|---|---|---|
| Docker | Engine 24+ con Compose v2 | `docker compose version` |
| Disco | ~8 GB | imagen ~4 GB (incluye torch) + modelo LLM ~5,6 GB + embeddings ~2,2 GB |
| RAM | 8 GB | |
| GPU | **Opcional pero muy recomendable** | NVIDIA con ≥6 GB de VRAM libres y NVIDIA Container Toolkit |

Sin GPU todo funciona, pero la inferencia corre en CPU y el enriquecimiento
tarda del orden de **diez veces más**. Es inherente al proyecto: el PRD exige
un LLM local y descarta explícitamente las APIs comerciales.

### Instalación

```bash
git clone https://github.com/luisgarciacasales/MOD1-PRACTICA.git
cd MOD1-PRACTICA

make where                      # confirma que se ejecutará en local
make init                       # crea data/ con el ownership correcto
cp .env.example .env
$EDITOR .env                    # POSTGRES_PASSWORD obligatorio; el resto opcional
```

`make init` **antes** del primer `up` no es opcional: si `data/` no existe,
Docker crea el bind mount como `root` y el contenedor —que corre con tu UID— no
puede escribir en Bronze. Comprueba también que `UID`/`GID` en el `.env`
coincidan con `id -u` / `id -g`.

El `.env` nunca entra al repositorio. Solo `POSTGRES_PASSWORD` es obligatorio.
`BANXICO_TOKEN` es gratuito (<https://www.banxico.org.mx/SieAPIRest/>) y sin él
la ingesta macro falla en soft mientras las otras fuentes continúan.

### El LLM: dos caminos

**A) Ya tienes Ollama corriendo en el host** — el `.env` funciona tal cual
(`host.docker.internal:11434`). Solo asegúrate del modelo:

```bash
ollama pull qwen3.5:9b
```

**B) No tienes Ollama** — el compose trae uno bajo un perfil desactivado por
defecto:

```bash
docker compose --profile standalone up -d
docker compose --profile standalone exec ollama ollama pull qwen3.5:9b
# y en el .env:  OLLAMA_BASE_URL=http://ollama:11434
```

Ese perfil está apagado a propósito. En el servidor del laboratorio se reusa un
Ollama compartido y levantar un segundo con los mismos modelos no cabría en
16 GB de VRAM (ADR-5); el perfil opcional permite que ambas cosas sean ciertas.
Si tu máquina tiene GPU, descomenta el bloque `deploy.resources` del servicio.

Cualquier modelo instruct de 7–9B cuantizado a 4 bits sirve: ajusta
`OLLAMA_MODEL_NER` y `OLLAMA_MODEL_MA`. Si eliges uno de razonamiento, revisa
[ADR-12](docs/ARQUITECTURA.md#42-los-modelos-del-prd-no-están-disponibles).

### Arranque

```bash
make up                         # postgres (pgvector) + app
make migrate                    # esquema Silver y Gold
make test                       # 130 pruebas — no necesitan red ni LLM
```

Si `make test` pasa, la instalación es correcta aunque aún no haya datos.

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
