
# Documento de Requisitos de Producto (PRD)

**Proyecto:** AI Engineering Lab — Analítica Avanzada de Equity BMV
**Fase:** 1 (Prueba de Concepto y Arquitectura Medallón Local)
**Fecha:** Julio 2026

## 1. Visión y Resumen Ejecutivo

La información pública de las empresas del sector financiero que cotizan en la Bolsa Mexicana de Valores (BMV) se encuentra altamente desestructurada. Esto dificulta la generación de *insights* de alta frecuencia sobre su posicionamiento estratégico, vulnerabilidades corporativas y adaptación tecnológica.

Este proyecto tiene como objetivo construir un motor de analítica e inteligencia artificial que relacione el desempeño en el mercado de valores con eventos corporativos extraídos de texto no estructurado. El valor principal de negocio radica en identificar oportunidades de Fusiones y Adquisiciones (M&A) y evaluar el nivel de competitividad de la banca tradicional frente a la disrupción de las Fintechs y neobancos en México.

## 2. Alcance (Scope)

### 2.1 En Alcance (Fase 1 - MVP Local)

* **Ingesta de Datos Multi-Fuente:** Scraping básico de "Eventos Relevantes" de la BMV, consumo de RSS de medios financieros mexicanos, carga del diccionario maestro de Fintechs (Finnovista Radar), Yahoo Finance API (`yfinance`) para datos de mercado, y API del Banco de México (BANXICO/SIE) para indicadores macroeconómicos.
* **Arquitectura Medallón Contenerizada:** Despliegue de capas Bronze, Silver y Gold utilizando Docker Compose.
* **Contratos de Datos Semánticos:** Validación de esquemas con Pydantic y enrutamiento de errores hacia una tabla de cuarentena (*Dead Letter Queue*). El contrato fuerza la existencia de al menos un Ticker, Sector o Entidad identificable; los registros huérfanos van a revisión manual.
* **Cargas Idempotentes:** Ingesta relacional con operaciones *UPSERT* por clave natural (`guid`) para evitar la duplicidad de registros en reprocesamientos.
* **Pipeline de Enriquecimiento NLP (Silver → Gold):** Un LLM local (Ollama + modelo cuantizado) ejecuta *Named Entity Recognition* (NER), análisis de sentimiento y detección explícita de eventos M&A sobre cada noticia validada antes de indexarla.
* **Indexación Vectorial en Español:** Generación de *embeddings* con modelos multilingües/español para habilitar búsqueda semántica contextualizada.
* **Calendario Bursátil y Correlación Temporal (Días Hábiles):** Uso de `pandas_market_calendars` (calendario XMEX) para resolver el problema de fines de semana y días feriados en México. El JOIN entre noticias y precios de mercado debe buscar el **siguiente día hábil de cotización** para reflejar correctamente el impacto en precio, no la fecha calendario de la noticia.
* **Proxy Ticker para Fintechs:** Cuando una noticia menciona un neobanco o fintech que no cotiza en la BMV (ej. Nu, Stori), el modelo debe inferir el **sector afectado** (banca de consumo, tarjetas de crédito, captación, crédito automotriz) y mapearlo a un **ticker proxy** que sí cotice (ej. GFNORTE, BBAJIO), permitiendo calcular el impacto real en el mercado mexicano.
* **Optimización de Inferencia con Asyncio:** Uso de `asyncio` + `aiohttp` para enviar **lotes de 8 noticias simultáneas** a Ollama, saturando la GPU local sin cambiar el hardware. Esto reduce el tiempo de enriquecimiento NLP de ~42 min a ~6-8 min para 500 noticias.
* **Caché de APIs Externas (requests-cache):** Implementación de `requests-cache` con backend SQLite desde el MVP para interceptar y cachear llamadas duplicadas a `yfinance` y BANXICO/SIE, evitando agotar límites de rate limiting accidentalmente durante pruebas iterativas de desarrollo.

### 2.2 Fuera de Alcance (Fase 2 - Escalamiento)

* Implementación de base de datos de grafos (Neo4j / GraphRAG).
* Despliegue de la infraestructura en Google Cloud Platform (BigQuery, Vertex AI, GCS).
* Llamadas a APIs de Modelos de Lenguaje Grandes (LLMs) externos comerciales (Gemini, Claude, DeepSeek).
* Pipelines masivos de extracción y *scraping* de PDFs o transcripciones de llamadas con inversionistas.
* **Terraform (IaC):** La gestión de infraestructura como código no aplica en Fase 1 (entorno local single-machine con Docker Compose). Será **requisito obligatorio en Fase 2** para el despliegue reproducible en GCP (BigQuery, Vertex AI, GCS, Cloud Run/GKE), incluyendo estado remoto en GCS y entornos separados (dev/staging/prod).

## 3. Fuentes de Datos

Se definen cinco fuentes agrupadas en dos categorías: **texto no estructurado** (noticias y eventos corporativos) y **datos estructurados de mercado** (precios, volúmenes e indicadores macroeconómicos). La ingesta de noticias es **batch diaria** al cierre del mercado mexicano (~15:30 h CT); los datos de mercado y macroeconómicos se ingieren en el mismo batch para garantizar alineación temporal.

### 3.1 Eventos Relevantes BMV (Scraping básico)

* **Origen:** Sección de "Eventos Relevantes" del portal web oficial de la BMV.
* **Extracción:** Scraping ligero del listado público (sin descarga de PDFs adjuntos).
* **Campos mínimos extraídos:** Título del evento, fecha de publicación, ticker(s) asociado(s).
* **Nota:** Esta fuente es la de menor estructura; puede presentar tickers embebidos en texto no etiquetado.

### 3.2 RSS / Feeds de Medios Financieros

* **Orígenes:**
  * El Financiero — Sección Mercados
  * El Economista — Sección Mercados/Empresas
  * Bloomberg Línea México
* **Ventaja:** Estos medios procesan y estructuran noticias crudas, ofreciendo feeds más limpios que la propia BMV. Incluyen titular, resumen, fecha y en ocasiones el ticker explícito.

### 3.3 Diccionario Maestro Fintech (Finnovista Radar)

* **Origen:** Finnovista Radar Fintech México.
* **Uso:** Diccionario estático de referencia con nombre legal, nombre comercial, ticker (si existe), y sector de Neobancos y Fintechs reguladas en México (Nu, Ualá, Stori, Klar, entre otras).
* **Frecuencia:** Carga única con actualización manual bajo demanda (no requiere scraping recurrente).
* **Propósito:** Cross-reference en la etapa de enriquecimiento NLP para etiquetar automáticamente noticias que involucren competencia Fintech vs. Banca Tradicional.
* **Proxy Ticker:** Debido a que los neobancos y fintechs listados en este diccionario (Nu, Ualá, Stori, Klar) **no cotizan en la BMV**, el motor de correlación no puede hacer JOIN directo con `gold_market_prices`. La solución es un mapeo de **sector afectado → ticker proxy**: el LLM infiere el sector impactado (banca de consumo, tarjetas de crédito, captación, crédito automotriz) y lo asocia a uno o más tickers que sí operan en la BMV. Ejemplo: noticia sobre "Nu lanza tarjeta de crédito" → sector `banca_consumo` → proxy `GFNORTE.MX`, `BBAJIO.MX`.
* **Mapeo sector→proxy (Fase 1):**

| Sector Afectado | Ticker(s) Proxy (BMV) | Justificación |
| --- | --- | --- |
| Banca de Consumo | GFNORTE.MX, BBAJIO.MX | Mayor exposición a crédito al consumo y tarjetas. |
| Captación / Ahorro | GFNORTE.MX | Líder en captación tradicional en México. |
| Crédito Automotriz | BBAJIO.MX | Fuerte presencia en financiamiento automotriz. |
| Pagos Digitales | GFNORTE.MX | Propietario de redes de TPV y banca digital. |
| Insurtech | WALMEX.MX | Proxy limitado; en Fase 2 se buscará ticker de aseguradora listada (ej. GNP). |

### 3.4 Yahoo Finance API — Datos de Mercado (yfinance)

* **Origen:** Yahoo Finance API, consumida mediante la librería Python `yfinance`.
* **Propósito:** Obtener la "otra mitad de la ecuación": precios de cierre, volúmenes, máximos/mínimos diarios e históricos de las emisoras financieras mexicanas. Sin estos datos, las noticias carecen de contexto cuantitativo de mercado.
* **Tickers prioritarios (Fase 1):** GFNORTE.MX, BBAJIO.MX, WALMEX.MX, AMXL.MX, GMEXICOB.MX, CEMEXCPO.MX, FEMSAUBD.MX, ALSEA.MX.
* **Campos extraídos por ticker:** `date`, `open`, `high`, `low`, `close`, `adj_close`, `volume`.
* **Ventana temporal:** Histórico de 2 años + actualización diaria al cierre.
* **Ventaja:** API gratuita para el MVP, sin necesidad de suscripción a terminales Bloomberg/Refinitiv. El sufijo `.MX` permite acceder directamente a la Bolsa Mexicana.
* **Nota:** `yfinance` depende de Yahoo Finance; no tiene SLA formal. Para Fase 1 es aceptable como fuente gratuita. En Fase 2 se migrará a un proveedor de datos de mercado con contrato de servicio (Bloomberg, Refinitiv o S&P Capital IQ).

### 3.5 API BANXICO (SIE) — Indicadores Macroeconómicos

* **Origen:** API REST del Sistema de Información Económica (SIE) del Banco de México.
* **Propósito:** Proveer contexto macroeconómico a las noticias y movimientos de mercado. Una noticia sobre la banca puede tener una lectura muy distinta en un entorno de tasas altas (TIIE) vs. tasas bajas.
* **Series económicas prioritarias (Fase 1):**
  * `SF43783` — TIIE a 28 días (tasa de referencia interbancaria).
  * `SF63528` — Tasa de fondeo gubernamental (tasa objetivo de Banxico).
  * `SF46410` — Tipo de cambio FIX USD/MXN (cierre de jornada).
  * `SF43718` — Tipo de cambio USD/MXN para liquidar obligaciones.
  * `SF617` — Agregado monetario M1 (circulante + cuentas de cheques).
  * `SF10770` — Inflación subyacente mensual (INPC).
* **Frecuencia de ingesta:** Diaria para tipo de cambio y tasas; mensual para agregados monetarios e inflación (se ingieren en cada batch diario, actualizando solo si BANXICO publicó nuevo dato).
* **Formato de respuesta:** JSON con serie temporal. Se aplana a tabla relacional en Silver.
* **Autenticación:** Token gratuito de BANXICO (requiere registro, sin costo para uso no comercial).

## 4. Arquitectura y Hardware

### 4.1 Entorno de Ejecución

El entorno de desarrollo local (Laboratorio) se ejecuta de manera nativa en Linux, maximizando el rendimiento del hardware sin incurrir en costos de nube durante la etapa iterativa.

* **Sistema Operativo:** Ubuntu 26.04 LTS.
* **Recursos de Hardware:**
  * GPU: NVIDIA RTX 5080 (16 GB VRAM)
  * CPU: AMD Ryzen 9 9900X 3D (16 núcleos)
  * RAM: 64 GB DDR5
  * Almacenamiento: 2 TB NVMe SSD
* **Orquestador:** Docker Compose para aislamiento y control de dependencias.
* **Runtime de Inferencia:** Ollama (con soporte GPU nativo en Docker) para modelos cuantizados a 4/8 bits.
* **Compatibilidad:** Docker con NVIDIA Container Toolkit ya configurado en el entorno.

### 4.2 Pila Tecnológica por Capa

| Capa Lógica | Tecnología | Propósito Funcional |
| --- | --- | --- |
| **Bronze (Raw)** | File System Local | Almacenamiento inmutable de la ingesta en formato JSON/Parquet con timestamp, simulando un Data Lake. Sin transformaciones de limpieza. |
| **Silver (Staging/DWH)** | PostgreSQL 15 | Almacenamiento estructurado con validaciones semánticas y comandos `ON CONFLICT` para cargas incrementales idempotentes. |
| **Contratos** | Python + Pydantic | Validación en memoria de tipos, longitudes, formatos y presencia de al menos un Ticker / Sector / Entidad antes de la escritura. |
| **NLP Bridge (Silver→Gold)** | Ollama + Qwen 2.5 / Llama 3 + asyncio | Inferencia local para NER, sentimiento y M&A. Llamadas asíncronas en lotes de 8 noticias simultáneas. Modelos cuantizados a 4 u 8 bits. |
| **Gold (Semántica)** | FAISS + multilingual-e5-large | Creación del índice `.index` de similitud vectorial y búsqueda semántica Top-K en español. |
| **Calendario Bursátil** | pandas_market_calendars (XMEX) | Resolución de siguiente día hábil de cotización para el JOIN noticias↔precios, evitando falsos negativos por fines de semana y feriados. |
| **Caché de APIs** | requests-cache (SQLite) | Intercepta y cachea llamadas duplicadas a `yfinance` y BANXICO durante desarrollo, previniendo rate limiting accidental. |

### 4.3 Diagrama de Flujo de Datos

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                            FUENTES DE DATOS                                   │
 │                                                                               │
 │  ┌──────────────── NOTICIAS (TEXTO) ────────────────┐  ┌─── MERCADO ───────┐ │
 │  │ ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────┐ │  │ ┌──────┐ ┌───────┐ │ │
 │  │ │ BMV       │ │ El Finan-│ │ El Econ- │ │Bloom-│ │  │ │Yahoo │ │BANXICO│ │ │
 │  │ │ Eventos   │ │ ciero    │ │ omista   │ │berg  │ │  │ │Finan-│ │ (SIE) │ │ │
 │  │ └─────┬─────┘ └────┬─────┘ └────┬─────┘ └──┬───┘ │  │ │ce    │ └───┬───┘ │ │
 │  │       └──────┬─────┴──────┬─────┴──────────┘     │  │ └──┬───┘     │     │ │
 │  └──────────────┼────────────┼──────────────────────┘  └────┼───────┼─────┘ │
 │                 │            │                              │       │        │
 └─────────────────┼────────────┼──────────────────────────────┼───────┼────────┘
                   │            │                              │       │
                   ▼            ▼                              ▼       ▼
 ┌──────────────────────────────────┐  ┌───────────────────────────────────────┐
 │    BRONZE — Raw News              │  │  BRONZE — Raw Market Data              │
 │    /bronze/news/{source}/{date}/  │  │  /bronze/market/{source}/{date}/      │
 │    ├── metadata.json              │  │  ├── metadata.json                    │
 │    ├── raw_payload.json           │  │  ├── prices.json / rates.json         │
 │    └── raw_payload.parquet        │  │  └── prices.parquet / rates.parquet   │
 └───────────────┬──────────────────┘  └──────────────────┬────────────────────┘
                 │                                        │
                 ▼                                        ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                         SILVER — Validación y Staging                          │
 │                                                                               │
 │  ┌──────────────────────────────────┐  ┌─────────────────────────────────┐   │
 │  │ CONTRATO Pydantic (SilverNews)   │  │ ESTRUCTURADOS (SilverMarket)    │   │
 │  │ ├── guid: str (PK)               │  │ ├── silver_market_prices        │   │
 │  │ ├── title, content, url          │  │ │   (ticker, date, open, high,  │   │
 │  │ ├── source, published_at         │  │ │    low, close, adj_close,     │   │
 │  │ ├── tickers, sector, entities    │  │ │    volume)                     │   │
 │  │ │                                │  │ │   PK: (ticker, date)          │   │
 │  │ │ VALIDACIÓN:                    │  │ │                               │   │
 │  │ │ • Tipo + Ticker/Sector/Entidad │  │ ├── silver_macro_indicators     │   │
 │  │ │ • OK → silver_news             │  │ │   (series_id, date, value)    │   │
 │  │ │ • FAIL → silver_dead_letters   │  │ │   PK: (series_id, date)       │   │
 │  │ │ • DUPE → UPSERT ON CONFLICT    │  │ │                               │   │
 │  └──────────────────────────────────┘  │ └── silver_fintech_dict          │   │
 │                                        └─────────────────────────────────┘   │
 └───────────────┬────────────────────────────────┬─────────────────────────────┘
                 │                                │
                 ▼                                │
 ┌──────────────────────────────────────┐         │
 │   NLP ENRICHMENT — Silver → Gold      │         │
 │  ┌────────────────┐ ┌──────────────┐  │         │
 │  │ Ollama + Qwen  │ │ Ollama+Llama │  │         │
 │  │ • NER          │ │ • M&A Detect │  │         │
 │  │ • Sentimiento  │ │ • Fintech Tag│  │         │
 │  └───────┬────────┘ └──────┬───────┘  │         │
 │          └────────┬─────────┘          │         │
 │                   ▼                    │         │
 │     gold_enriched_news (PostgreSQL)    │         │
 └───────────────────┬───────────────────┘         │
                     │                             │
                     ▼                             ▼
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │                          GOLD — Semantic + Market Layer                        │
 │                                                                               │
 │  ┌─────────────────────────┐  ┌────────────────────────────────────────────┐ │
 │  │ FAISS IndexFlatIP       │  │ Tablas Gold (PostgreSQL)                   │ │
 │  │  • multilingual-e5-large│  │  ├── gold_enriched_news                     │ │
 │  │  • Búsqueda Top-K       │  │  │   (embeddings, NER, M&A, sentimiento)   │ │
 │  │  • Distancia coseno     │  │  ├── gold_market_prices                     │ │
 │  └─────────────────────────┘  │  │   (precios + retornos calculados)       │ │
 │                                │  ├── gold_macro_indicators                 │ │
 │  API de consulta:              │  │   (tasas normalizadas, YoY)             │ │
 │  search_semantic(query, k=10)  │  └── gold_news_market_corr                 │ │
 │  get_market_context(ticker)    │      (JOIN noticias↔precios↔macro)         │ │
 └────────────────────────────────┴────────────────────────────────────────────┘
```

### 4.4 Flujo de Datos — Descripción Paso a Paso

1. **Ingesta (Origen → Bronze):** El scheduler diario (post-cierre de mercado ~15:30 CT) dispara la ingesta de las 5 fuentes en dos pipelines paralelos:
   - **Pipeline de Noticias (Texto):** BMV, El Financiero, El Economista y Bloomberg Línea MX. Cada lote se almacena en `/data/bronze/news/{source}/{YYYY-MM-DD}/{batch_id}/` en formato JSON y Parquet. **No se aplica ninguna transformación de limpieza.**
   - **Pipeline de Mercado (Estructurado):** Yahoo Finance (`yfinance`) y BANXICO (SIE). Las respuestas JSON de las APIs se almacenan en `/data/bronze/market/{source}/{YYYY-MM-DD}/{batch_id}/`. Los datos de mercado son inherentemente estructurados (filas × columnas), por lo que su contrato es puramente de tipos y rangos, no semántico.
   - Ambos pipelines son *fail-soft*: si una fuente falla, el resto continúa.

2. **Validación (Bronze → Silver):**
   - **Noticias:** El validador lee cada registro desde Bronze, aplica el contrato Pydantic `SilverNews` en dos dimensiones: tipado estricto (tipos, longitudes, fechas, URLs) e integridad semántica (presencia de al menos un Ticker, Sector o Entidad). Registros huérfanos van a `silver_dead_letters` con `MISSING_ENTITY`.
   - **Mercado:** Contrato Pydantic `MarketPrice` y `MacroIndicator` con validación de tipos, rangos (ej. precio > 0, volumen ≥ 0) y unicidad de clave compuesta `(ticker, date)` o `(series_id, date)`. Datos inválidos van a `silver_dead_letters` con motivo específico.

3. **Carga Idempotente (Silver → PostgreSQL):** Los registros que pasan el contrato se insertan en sus tablas respectivas (`silver_news`, `silver_market_prices`, `silver_macro_indicators`) mediante `INSERT ... ON CONFLICT ... DO UPDATE`. Reprocesar el mismo batch produce `filas_nuevas = 0` en todas las tablas.

4. **Enriquecimiento NLP (Silver → Gold, solo noticias):** Un proceso batch toma los registros de `silver_news` con `enriched = false` y los envía a Ollama para inferencia local en dos pasos: NER + Sentimiento, y M&A + Fintech tagging. El resultado se persiste en `gold_enriched_news`.

5. **Transformación Gold (Market Data):** Los datos de `silver_market_prices` se enriquecen con retornos diarios calculados (`daily_return_pct`), medias móviles (7d, 30d) y volatilidad. Los indicadores macro (`silver_macro_indicators`) se normalizan con variaciones interanuales (YoY). Ambos se persisten en `gold_market_prices` y `gold_macro_indicators`.

6. **Correlación Noticias ↔ Mercado (Gold):** Un proceso de cierre de batch ejecuta un JOIN temporal entre `gold_enriched_news` y `gold_market_prices` usando el calendario bursátil **XMEX** (`pandas_market_calendars`) para resolver el siguiente día hábil. Esto corrige el problema de fines de semana y feriados (ej. noticia del viernes → impacto medido el lunes). El JOIN se realiza en dos modalidades:
   - **Directo:** si la noticia tiene un ticker que cotiza en BMV (GFNORTE, WALMEX, etc.), se asocia directamente.
   - **Proxy:** si la noticia menciona una Fintech sin cotización (Nu, Stori), el LLM infiere el sector afectado y lo mapea a un ticker proxy (GFNORTE o BBAJIO) usando la tabla de mapeo definida en 3.3. El campo `is_proxy = true` permite distinguir estos casos en análisis posteriores.
   - El resultado se persiste en `gold_news_market_corr` y permite consultas como: *"noticias con sentimiento negativo sobre GFNORTE (o fintechs que le compiten) seguidas de una caída >2% en el precio de cierre del siguiente día hábil"*.

7. **Indexación (Gold → FAISS):** El indexador lee los embeddings generados y construye o actualiza incrementalmente el índice FAISS. Expone `search_semantic(query, top_k)` y `get_market_context(ticker)` para consultas combinadas.

## 5. Modelo de Datos

### 5.1 Esquema Bronze — Raw Ingest

```yaml
bronze_batch:
  path: /data/bronze/{source}/{date}/{batch_id}/
  files:
    - metadata.json:
        batch_uuid: str (UUID4)
        source: enum[bmv_eventos|financiero|economista|bloomberg|yahoo_finance|banxico]
        ingested_at: datetime (ISO 8601, UTC)
        record_count: int
        checksum_sha256: str
    - raw_payload.json:
        Array de objetos exactamente como se recibieron (RSS XML→JSON, HTML scrapeado→JSON, o respuesta de API REST→JSON)
    - raw_payload.parquet:
        Mismo contenido en formato columnar para analytics exploratorio
```

### 5.2 Esquema Silver — Validated News

```yaml
silver_news:
  guid: str, PK, natural key (hash SHA256 de source + url + published_at)
  source: enum[bmv_eventos|financiero|economista|bloomberg]
  title: str, NOT NULL, max 1024
  content: str, NOT NULL, max 8192
  url: str, format URL, NOT NULL
  published_at: datetime, NOT NULL
  ingested_at: datetime, NOT NULL, default NOW()
  tickers: list[str] | None       # extraídos de la fuente (ej. ["GFNORTE","WALMEX"])
  sector: str | None              # ej. "banca","retail","telecom"
  entities: list[str] | None      # personas o empresas mencionadas
  enriched: bool, default false   # flag: ya fue procesado por NLP Bridge
  macro_bypass: bool, default false  # true si la noticia calificó para bypass macroeconómico
  raw_batch_uuid: str, FK → Bronze

silver_dead_letters:
  id: serial, PK
  guid: str
  source: enum
  raw_payload: JSONB              # registro original completo
  rejection_reason: str           # ej. "MISSING_ENTITY","INVALID_URL","TYPE_MISMATCH"
  rejected_at: datetime, default NOW()
  batch_uuid: str, FK → Bronze

silver_fintech_dict:
  id: serial, PK
  legal_name: str, NOT NULL       # Razón social
  commercial_name: str, NOT NULL  # Nombre comercial (Nu, Ualá, Stori, Klar...)
  ticker: str | None
  sector: str                     # "neobanco","lending","payments","insurtech"
  country: str, default "MX"
  updated_at: datetime

silver_market_prices:
  id: serial, PK
  ticker: str, NOT NULL           # Ej. "GFNORTE.MX"
  date: date, NOT NULL
  open: float, NOT NULL, > 0
  high: float, NOT NULL, > 0
  low: float, NOT NULL, > 0
  close: float, NOT NULL, > 0
  adj_close: float, NOT NULL, > 0
  volume: int, NOT NULL, ≥ 0
  ingested_at: datetime, NOT NULL, default NOW()
  raw_batch_uuid: str, FK → Bronze
  UNIQUE (ticker, date)           # Clave compuesta para idempotencia

silver_macro_indicators:
  id: serial, PK
  series_id: str, NOT NULL        # Ej. "SF43783" (TIIE 28d)
  date: date, NOT NULL
  value: float, NOT NULL
  ingested_at: datetime, NOT NULL, default NOW()
  raw_batch_uuid: str, FK → Bronze
  UNIQUE (series_id, date)        # Clave compuesta para idempotencia
```

### 5.3 Esquema Gold — Enriched Semantic News

```yaml
gold_enriched_news:
  id: serial, PK
  guid: str, FK → silver_news, UNIQUE
  # --- Campos heredados de Silver ---
  source: enum
  title: str
  content: str
  url: str
  published_at: datetime
  ingested_at: datetime

  # --- Embedding ---
  embedding: vector(1024)                     # intfloat/multilingual-e5-large

  # --- NER Output ---
  ner_tickers: list[str] | None               # ["GFNORTE","WALMEX"] detectados por LLM
  ner_persons: list[str] | None
  ner_orgs: list[str] | None
  ner_sectors: list[str] | None               # ["banca","energía","telecom"]

  # --- Sentiment ---
  sentiment_score: float, range [0.0, 1.0]
  sentiment_label: enum[positive|negative|neutral]

  # --- M&A Detection ---
  is_ma_event: bool, default false
  ma_event_type: enum[acquisition|merger|partnership|none], default none
  ma_confidence: float, range [0.0, 1.0]      # confianza del clasificador

  # --- Fintech Tagging ---
  fintech_flag: bool, default false            # ¿menciona fintechs del diccionario Finnovista?
  fintechs_identified: list[str] | None        # ["Nu","Stori"]
  traditional_banks_mentioned: list[str] | None

  # --- Metadata ---
  enriched_at: datetime, NOT NULL
  model_version: str                           # versión del modelo LLM usado

gold_market_prices:
  id: serial, PK
  ticker: str, NOT NULL
  date: date, NOT NULL
  open: float, NOT NULL
  high: float, NOT NULL
  low: float, NOT NULL
  close: float, NOT NULL
  adj_close: float, NOT NULL
  volume: int, NOT NULL
  daily_return_pct: float                     # retorno diario porcentual
  ma_7d: float | None                         # media móvil 7 días
  ma_30d: float | None                        # media móvil 30 días
  volatility_30d: float | None                # volatilidad 30 días (std dev retornos)
  ingested_at: datetime, NOT NULL
  UNIQUE (ticker, date)

gold_macro_indicators:
  id: serial, PK
  series_id: str, NOT NULL
  series_name: str, NOT NULL                  # Ej. "TIIE a 28 días"
  date: date, NOT NULL
  value: float, NOT NULL
  yoy_change_pct: float | None                # variación interanual
  ingested_at: datetime, NOT NULL
  UNIQUE (series_id, date)

gold_news_market_corr:
  id: serial, PK
  news_guid: str, FK → gold_enriched_news
  ticker: str, NOT NULL                       # ticker detectado por NER o fuente
  is_proxy: bool, default false               # true si el ticker es un proxy (fintech → banca)
  proxy_ticker: str | None                    # ticker BMV real si is_proxy=true (ej. GFNORTE.MX)
  original_fintech: str | None                # fintech mencionada si es proxy (ej. "Nu")
  sector_affected: str | None                 # sector inferido para proxy (ej. "banca_consumo")
  news_date: date, NOT NULL                   # fecha de la noticia
  next_trading_day: date, NOT NULL            # siguiente día hábil bursátil según XMEX
  price_date: date, NOT NULL                  # fecha del precio usada (next_trading_day)
  close_price: float                          # precio de cierre en price_date
  next_day_return_pct: float | None           # retorno del día siguiente hábil a la noticia
  price_change_5d_pct: float | None           # cambio acumulado en 5 días hábiles post-noticia
  macro_context: JSONB | None                 # tasas/tipo de cambio vigentes en news_date
  UNIQUE (news_guid, ticker, price_date)
```

## 6. Requisitos de Ingeniería de Datos

1. **Trazabilidad e Inmutabilidad (Capa Bronze):** Cada lote de ingesta se guarda en formato JSON y Parquet de forma íntegra, sin aplicar ninguna transformación de limpieza. El `metadata.json` adjunto garantiza trazabilidad con batch_uuid, source, timestamp y checksum SHA-256.

2. **Tolerancia a Fallos y Calidad Semántica (Capa Silver):** Todo registro se evalúa contra un esquema `BaseModel` de Pydantic en dos niveles:
   - **Tipado estricto:** tipos, longitudes, formatos de fecha, URLs válidas.
   - **Integridad semántica:** el contrato fuerza que el registro tenga al menos un Ticker, Sector o Entidad. **Excepción — Bypass Macroeconómico:** noticias de alto impacto sobre tasas de interés, inflación o política monetaria (detectadas por el LLM o por `source = bloomberg`) pueden no mencionar un ticker específico. En ese caso, el validador asigna `macro_bypass = true` y el registro pasa a `silver_news` aunque `tickers` esté vacío, evitando falsos negativos que eliminarían contexto macroeconómico crítico.
   - Si un registro no cumple y no aplica bypass, se envía a `silver_dead_letters` con el motivo específico del rechazo.

3. **Cargas Idempotentes:** El motor relacional utiliza una clave natural (`guid`, hash SHA-256 de source + url + published_at) para gestionar las inserciones mediante `INSERT ... ON CONFLICT (guid) DO UPDATE`. Reprocesar el mismo lote debe producir `filas_nuevas = 0`.

4. **Enriquecimiento NLP Local (Silver → Gold):** La arquitectura lee registros no procesados de Silver y los somete a un pipeline de inferencia local con Ollama usando **llamadas asíncronas en lotes de 8 noticias simultáneas** (`asyncio` + `aiohttp`) para saturar la GPU. El pipeline ejecuta:
   - NER multilingüe (extracción de tickers, personas, organizaciones, sectores).
   - Análisis de sentimiento (positivo/negativo/neutral con score).
   - Detección explícita de eventos M&A (adquisición, fusión, alianza estratégica).
   - Cross-reference con el diccionario Finnovista para etiquetar competencia Fintech vs. Banca Tradicional.
   - **Proxy Ticker inference:** si se detecta una Fintech sin cotización en BMV, el LLM infiere el sector afectado y lo mapea a un ticker proxy (GFNORTE, BBAJIO) usando la tabla de mapeo definida en 3.3.

5. **Indexación Vectorial en Español (Capa Gold):** Los registros enriquecidos se vectorizan con `intfloat/multilingual-e5-large` (entrenado en múltiples idiomas incluyendo español, contraste con modelos monolingües en inglés que destruyen contexto). El índice FAISS se persiste en NVMe SSD y expone una API de búsqueda Top-K por similitud coseno.

6. **Calendario Bursátil para Correlación Temporal:** El JOIN entre `gold_enriched_news` y `gold_market_prices` debe usar `pandas_market_calendars` con el calendario **XMEX** (Bolsa Mexicana de Valores) para resolver el siguiente día hábil de cotización. Si una noticia se publica un viernes a las 16:00, el impacto en precio debe medirse el lunes siguiente (o el próximo día hábil si el lunes es feriado). Esto evita falsos negativos por fines de semana y días feriados en México.

7. **Caché de APIs Externas:** Toda llamada a `yfinance` y BANXICO/SIE debe pasar por `requests-cache` con backend SQLite local. Durante el desarrollo iterativo, las llamadas repetidas al mismo ticker/fecha/serie devuelven la respuesta cacheada sin consumir cuota de API. El caché se invalida por TTL diario para datos de mercado y semanal para series macroeconómicas mensuales.

## 7. Rendimiento y SLAs

Los siguientes tiempos de respuesta se alinean con estándares de la industria financiera para analítica post-cierre de mercado.

| Métrica | SLA Objetivo | Fundamento |
| --- | --- | --- |
| **Ingesta batch completa** (5 fuentes) | < 15 min desde cierre (~15:30 CT) | Las APIs de mercado (yfinance, BANXICO) son rápidas (~2-5 s por ticker/serie). Las fuentes de noticias dominan el tiempo. |
| **Validación Silver** (lote ≤ 500 noticias) | < 1 min | El cuello de botella es el parsing; Pydantic opera en memoria. |
| **Enriquecimiento NLP por lote** (500 noticias, batch async ×8) | < 10 min total | asyncio + Ollama con Qwen 2.5 4-bit en RTX 5080. Lotes de 8 noticias simultáneas. ~1.2 s por noticia en promedio con paralelismo. |
| **Indexación FAISS** (batch incremental) | < 30 s para ≤ 500 vectores | Índice FlatIP en NVMe; operación ligera. |
| **Búsqueda semántica Top-K (K=10)** | < 500 ms | Lectura del índice desde NVMe SSD; FAISS optimizado para baja latencia. |
| **Correlación noticias↔mercado** (JOIN + proxy + calendario) | < 5 s para 500 noticias | Consulta SQL con window functions sobre PostgreSQL; `pandas_market_calendars` precargado en memoria. |
| **Disponibilidad Gold** (datos enriquecidos + correlacionados) | < 30 min post-cierre de mercado | Ingesta (15 min) + validación (1 min) + NLP async ×8 (10 min) + correlación (1 min) + indexación (1 min) con margen. |

**Nota sobre el enriquecimiento NLP:** El uso de `asyncio` con lotes de 8 noticias simultáneas permite saturar la RTX 5080 sin exceder la VRAM. Con procesamiento secuencial, 500 noticias tomarían ~42 min; con el enfoque async batch se reducen a ~6-8 min. Si el volumen diario crece, se escala a 16 noticias simultáneas (requiere monitorear uso de VRAM).

## 8. Criterios de Aceptación (Definición de "Terminado")

El desarrollo de esta Fase 1 se considerará exitoso (*Done*) cuando el entorno pueda levantarse de punta a punta y cumplir estrictamente con los siguientes umbrales de validación técnica, demostrables mediante la ejecución de un *script* o *notebook*:

| Criterio | Mínimo Esperado |
| --- | --- |
| **Bronze** | **5 fuentes ingeridas** (BMV Eventos, RSS Financieros [3 medios], Finnovista, Yahoo Finance, BANXICO), **2+ lotes diarios**, almacenados con timestamp, y estrictamente **sin transformar**. Los datos de mercado deben incluir precios OHLCV y series macroeconómicas BANXICO. |
| **Contrato Semántico** | **Validación explícita** de tipado y presencia de Ticker/Sector/Entidad. Registros fallidos enviados a **cuarentena con motivo**. Registros sin entidad identificable deben aparecer en `silver_dead_letters` con `MISSING_ENTITY`. **Bypass macroeconómico funcional:** al menos 1 noticia macro sin ticker debe pasar a `silver_news` con `macro_bypass = true`. |
| **Idempotencia** | El reproceso del mismo lote da como resultado `filas_nuevas = 0`. |
| **Duplicados** | La consulta `SELECT guid, COUNT(*) FROM silver_news GROUP BY guid HAVING COUNT(*) > 1` da como resultado **0 filas**. |
| **NLP Enrichment** | **NER funcional** extrayendo tickers y entidades sobre noticias en español. **Sentimiento** asignado (positivo/negativo/neutral). **Detección M&A** con al menos 1 caso positivo identificado. **Fintech tagging** activo contra diccionario Finnovista. **Proxy ticker funcional:** al menos 1 noticia sobre Fintech sin cotización BMV debe generar un `is_proxy = true` con ticker proxy asignado. **Async batch demostrado:** el pipeline de NLP debe ejecutarse con `asyncio` enviando lotes de 8 noticias simultáneas a Ollama. |
| **Correlación Temporal** | **Calendario XMEX operativo:** el JOIN noticias↔precios debe usar `pandas_market_calendars` para resolver el siguiente día hábil. Una noticia de viernes debe tener `price_date` = lunes (o siguiente hábil). Demostrable con al menos 1 caso. |
| **Gold** | **Índice vectorial FAISS funcional**, construido con `multilingual-e5-large`, con **1+ consulta semántica demostrada en español** devolviendo resultados relevantes. **Correlación noticias↔mercado** funcional: al menos 1 consulta demostrada que relacione sentimiento de noticia con movimiento de precio del ticker. |

## 9. Riesgos Identificados y Mitigaciones

| Riesgo | Probabilidad | Impacto | Mitigación |
| --- | --- | --- | --- |
| **Cold start de modelos LLM en Docker:** Carga inicial de Qwen/Llama cuantizado puede tomar >30 s. | Alta | Medio | Healthcheck con warm-up al levantar el contenedor. Caché de modelo en volumen Docker persistente. |
| **VRAM insuficiente (16 GB):** Modelos más grandes (13B+) no caben ni cuantizados a 4-bit. | Media | Alto | Usar exclusivamente modelos 7B-8B cuantizados a 4-bit (≈5-6 GB VRAM). Deja margen para embeddings y batch inference. |
| **Scraping BMV frágil:** Cambios en el DOM de la web de la BMV rompen el extractor. | Alta | Medio | El pipeline es *fail-soft* por fuente. Si BMV falla, las demás fuentes (RSS, Yahoo Finance, BANXICO) continúan. Alertas de monitoreo sobre tasa de éxito de scraping. |
| **Calidad del español en modelos:** Sentence Transformers genéricos en inglés no entienden jerga financiera mexicana. | Media | Alto | Usar `intfloat/multilingual-e5-large` que fue entrenado con corpus multilingüe. Evaluar fine-tuning futuro con corpus BMV. |
| **AmbIGUEDAD de tickers:** Un mismo texto puede mencionar tickers sin ser noticia relevante para ese ticker. | Media | Medio | El NER del LLM debe contextualizar. Registrar `ma_confidence` y permitir filtro por umbral. |
| **Volumen de noticias subestimado:** Si los feeds RSS producen >500 noticias/día, el SLA de NLP se degrada. | Baja | Bajo | Batch inference con workers paralelos. Escalar a 2 workers reduce tiempo ~50%. |
| **Rate limiting de Yahoo Finance (yfinance):** La API no documenta límites formales, pero puede aplicar throttling o bloquear IPs por exceso de solicitudes. | Media | Medio | `requests-cache` con backend SQLite cachea automáticamente todas las llamadas durante desarrollo. En producción, espaciar solicitudes con sleep (0.5-1 s). Lista de tickers acotada (8-12). |
| **Token BANXICO expirado o rechazado:** El token gratuito del SIE puede expirar o requerir revalidación. | Baja | Alto | Healthcheck pre-ingesta que valide el token contra un endpoint ligero (ej. metadata de una serie). Alertar si falla. |
| **Desfase temporal mercado vs. noticias:** Yahoo Finance puede reflejar el precio de cierre con retraso de minutos u horas. | Media | Bajo | La ingesta es post-cierre (~15:30 CT + 15 min de margen). Verificar `adj_close` vs. `close` y documentar la fecha exacta del dato. |
