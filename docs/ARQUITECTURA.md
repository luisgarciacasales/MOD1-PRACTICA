# Arquitectura — MOD1-PRACTICA

Cómo está construido el sistema, qué decisiones se tomaron y **en qué se aparta
del PRD y por qué**. El PRD (`docs/PRD.md`) describe lo que se quería construir;
este documento describe lo que se construyó y justifica cada diferencia.

Las decisiones del arnés de agentes viven en `docs/HARNESS.md` como ADR
numerados; aquí se referencian en vez de duplicarse.

---

## 1. Vista general

Cuatro capas sobre una única máquina, orquestadas con Docker Compose.

| Capa | Tecnología | Función |
|---|---|---|
| **Bronze** | Sistema de archivos (JSON + Parquet) | Ingesta cruda, inmutable, con trazabilidad por checksum |
| **Silver** | PostgreSQL 15 + Pydantic | Validación de contrato y carga idempotente |
| **Gold** | PostgreSQL + pgvector | Enriquecimiento NLP, métricas de mercado y correlación temporal |
| **Semántica** | FAISS `IndexFlatIP` | Búsqueda Top-K por similitud coseno en español |

Dos contenedores: `app` (el pipeline) y `postgres`. El runtime de inferencia
**no** está en este compose: se reusa un contenedor Ollama compartido del host,
alcanzado por `host.docker.internal` (ADR-5). El motivo es la VRAM: 16 GB no
admiten dos copias de los mismos modelos, y ese es el riesgo alto nº2 del PRD.

Todos los puertos se publican en `127.0.0.1` y se accede por túnel SSH.

## 2. Las ocho etapas

| Etapa | Entrada → Salida | Garantía |
|---|---|---|
| `migrate` | `sql/*.sql` → esquema | Idempotente, con registro en `schema_migrations` |
| `ingest` | 5 fuentes → Bronze | Inmutable, sin transformar, *fail-soft* por fuente |
| `validate` | Bronze → Silver | Contrato Pydantic; rechazo tipado a cuarentena; UPSERT |
| `enrich` | `silver_news` → `gold_enriched_news` | LLM local, lotes async, salida validada contra vocabulario cerrado |
| `transform` | `silver_market_*` → `gold_market_*` | Window functions en SQL |
| `correlate` | Gold ⋈ Gold → `gold_news_market_corr` | JOIN temporal con calendario XMEX |
| `index` | `gold_enriched_news` → pgvector + FAISS | Vectores normalizados, índice exacto |
| `verify` | Todo → informe | 17 checks PASS/FAIL de la Definición de Terminado |

Cada una se invoca por separado (`make <etapa>`) y todas son idempotentes.

### Claves naturales

La idempotencia descansa en que cada tabla tiene una clave que identifica el
dato, no la ejecución:

| Tabla | Clave natural |
|---|---|
| `silver_news`, `gold_enriched_news` | `guid` = SHA-256 de `source + url + published_at` |
| `silver_market_prices`, `gold_market_prices` | `(ticker, date)` |
| `silver_macro_indicators`, `gold_macro_indicators` | `(series_id, date)` |
| `silver_fintech_dict` | `(commercial_name, country)` |
| `gold_news_market_corr` | `(news_guid, ticker, price_date)` |

Toda carga usa `INSERT … ON CONFLICT … DO UPDATE`, y las filas nuevas se cuentan
con `RETURNING (xmax = 0)` — PostgreSQL pone `xmax` a 0 en una inserción real y
distinto de 0 cuando el UPSERT resolvió un conflicto. Eso distingue inserción de
actualización en una sola pasada, sin un `SELECT` previo susceptible de carreras.

### Defensa en profundidad

Las reglas del contrato viven **dos veces**: como validadores Pydantic y como
`CHECK` en PostgreSQL. No es redundancia ociosa: Pydantic protege la ruta del
pipeline y los `CHECK` protegen la tabla de cualquier otra escritura —un `psql`
manual, un script futuro, un reproceso a medias—. Está verificado saltándose
Pydantic e insertando por `psql`: la base rechaza el huérfano semántico, el
`guid` mal formado, el OHLC incoherente y el motivo de rechazo inventado.

---

## 3. Fuentes: estado real

Verificado desde el contenedor el 2026-08-02.

| Fuente | Estado | Detalle |
|---|---|---|
| El Financiero | ✅ | Feed general; 100 entradas |
| Bloomberg Línea | ✅ | Categoría *mercados*; requiere `?outputType=xml` |
| Finnovista | ✅ | Semilla versionada en `seed/`, 20 fintechs |
| Yahoo Finance | ✅ | 8 emisoras `.MX`, 2 años, 4 008 filas |
| BANXICO SIE | ✅ | 6 series, histórico de 2 años, 2 000 puntos |
| **El Economista** | ❌ | HTTP 403 con y sin cabeceras de navegador: el WAF bloquea IPs de datacenter |
| **BMV Eventos Relevantes** | ❌ | SPA; el listado lo pinta JavaScript y los endpoints internos devuelven 404 en su gateway WSO2 |

Las dos caídas son el **riesgo nº3 del PRD §9** —*"scraping BMV frágil",
probabilidad Alta*— materializándose, y su mitigación documentada es
precisamente el *fail-soft* por fuente que está implementado.

Salidas posibles, ninguna dentro del alcance de Fase 1: capturar el XHR real de
la BMV con las devtools de un navegador, un navegador headless (que el PRD §2.2
excluye), o un acuerdo de uso con la BMV / otro medio en lugar de El Economista.

---

## 4. Divergencias respecto al PRD

Cada una surgió de ejecutar contra el mundo real, no de reinterpretar el
documento. Se registran aquí en vez de corregir el PRD para que quede constancia
de qué se encontró y por qué se decidió lo que se decidió.

### 4.1 Tres de los ocho tickers del PRD no existen

El PRD §3.4 lista `GFNORTE.MX`, `BBAJIO.MX` y `AMXL.MX`. Yahoo Finance devuelve
**serie vacía** para los tres, porque exige la serie accionaria en el símbolo:

| PRD | Real | Motivo |
|---|---|---|
| `GFNORTE.MX` | `GFNORTEO.MX` | Banorte cotiza la serie O |
| `BBAJIO.MX` | `BBAJIOO.MX` | Banco del Bajío, serie O |
| `AMXL.MX` | `AMXB.MX` | América Móvil convirtió las series L a B |

Los otros cinco eran correctos. **Consecuencia si no se corrige:** la ingesta de
mercado devolvería series vacías *sin error*, y el fallo aparecería mucho más
tarde como un JOIN que no casa. La corrección se aplicó también al mapeo
sector→proxy, que referenciaba los símbolos erróneos. (ADR-11)

### 4.2 Los modelos del PRD no están disponibles

El PRD §4.2 nombra *Qwen 2.5 / Llama 3*. Ninguno está descargado en el Ollama
compartido. Del catálogo disponible, el único dentro del presupuesto de VRAM de
la invariante «7–8B cuantizados a 4 bits» es **`qwen3.5:9b`** (9,7 B Q4_K_M,
≈5,6 GB); el resto (`gemma4:12b`, `qwen3:14b`, `mistral-small:22b`) lo excede.

Además, `qwen3.5` es un **modelo de razonamiento**: por defecto emite su cadena
de pensamiento en el campo `thinking` y agota el presupuesto de tokens sin
escribir nada en `content`. Se envía `think: false`. También envuelve el JSON en
vallas de Markdown pese a `format: "json"`, así que el cliente las despega antes
de parsear. (ADR-8, ADR-12)

### 4.3 El bypass macroeconómico se endureció

El PRD §6.2 detecta las noticias macro *"por el LLM o por `source = bloomberg`"*.
Leído al pie de la letra, la fuente sola convierte el bypass en un agujero:
**cualquier** nota huérfana de Bloomberg Línea entraría a `silver_news` sin
ticker, sector ni entidad, y el contrato dejaría de filtrar esa fuente.

Implementado: el léxico macro es **obligatorio** (umbral de 2 términos
distintos) y la fuente solo *refuerza*, bajando el umbral a 1. Cero términos
nunca activa el bypass. Decisión tomada con el usuario. (ADR-10)

Un detalle que costó un fallo real: la comparación debe ser **por frontera de
palabra**. Con coincidencia por subcadena, `"fed"` casaba dentro de
*"con**fed**eración"* y una nota sobre la FIFA entró a Silver como
macroeconómica. Era el término más disparado de todo el corpus.

### 4.4 Extracción léxica antes del NER

Los feeds RSS **no traen tickers etiquetados**. El contrato exige al menos un
Ticker, Sector o Entidad identificable, así que sin ningún mecanismo previo
prácticamente todas las noticias irían a cuarentena y `enrich` se quedaría sin
insumo — el pipeline se estrangularía a sí mismo.

El PRD describe `tickers` como *"extraídos de la fuente"*, y la fuente los trae
**dentro del texto**, no en un campo. Se implementó identificación léxica por
diccionario (`src/config/emisoras.py`) que resuelve la pregunta binaria *¿es
identificable?*. No sustituye al NER: el LLM escribe sus propios `ner_tickers`
en Gold, con contexto semántico.

El sesgo es **deliberadamente generoso**: un falso positivo lo descarta el LLM
después; un falso negativo manda la noticia a cuarentena y la pierde. El primero
es reversible, el segundo no. El coste se ve: una nota titulada «Lactancia
materna» quedó etiquetada `FEMSAUBD.MX` porque el cuerpo menciona Fundación
FEMSA.

### 4.5 Limpieza de HTML antes de validar longitud

El contrato limita `content` a 8 192 caracteres (PRD §5.2), pero los feeds
entregan el **artículo completo en HTML**: 10 500 caracteres de media, hasta
25 622. El resultado inicial fueron 330 de 400 noticias rechazadas por
`TYPE_MISMATCH` — un rechazo por *formato* disfrazado de rechazo por *calidad*.

La normalización Bronze→Silver ahora quita el markup y, si aún excede, recorta
por frontera de palabra conservando el principio de la nota (pirámide
invertida). Bronze conserva el HTML íntegro, así que no se pierde nada. Tras el
cambio, `TYPE_MISMATCH` bajó de 171 a 6, y esos 6 son filas con `NaN` de Yahoo,
que sí son dato malo.

### 4.6 Coherencia OHLC — validación añadida

El PRD §5.2 solo exige `precio > 0` y `volumen ≥ 0`. Una fila con
`low = 200, high = 150` pasa esa regla y aun así es basura que envenenaría los
retornos, las medias móviles y la volatilidad de Gold durante 30 sesiones.

Añadido: `low ≤ high` y `open`/`close` dentro del rango del día, en el contrato
y como `CHECK` en la tabla.

### 4.7 El `guid` se normaliza a UTC antes del hash

El PRD define la clave natural como SHA-256 de `source + url + published_at`,
sin decir nada de husos horarios. Los RSS mexicanos mezclan offsets: sin
normalizar, **el mismo artículo reingerido con otra representación horaria
produce otro `guid`** y la idempotencia se rompe en silencio. Hay una prueba
dedicada a esto.

### 4.8 BANXICO: histórico en lugar del último dato

El PRD §3.5 describe ingesta diaria. `/datos/oportuno` devuelve **un solo
punto**, con el que no se puede calcular el `yoy_change_pct` que
`gold_macro_indicators` exige (PRD §5.3) ni resolver el `macro_context` de una
noticia con fecha pasada. Se pide el rango histórico alineado con la ventana de
precios, con reserva a `oportuno` si una serie rechaza el rango.

Dos trampas del SIE que costaron tiempo: negocia contenido por cabecera `Accept`
y devuelve **XML** por defecto —hay que pedir `application/json` explícitamente—
y el rango va en la ruta en formato **ISO `aaaa-mm-dd`**, aunque la API devuelva
las fechas de los datos en `dd/mm/aaaa`; usar el formato equivocado da 404, no
400.

### 4.9 Las modalidades directa y proxy conviven

El PRD §4.4 presenta el JOIN en dos modalidades y podría leerse como
excluyentes. Una misma noticia puede mencionar a FEMSA **y** a Mercado Pago: el
ticker directo es `FEMSAUBD.MX` y el proxy por pagos digitales es
`GFNORTEO.MX`. Son emisoras distintas y señales distintas; tratarlas como
excluyentes descartaba la segunda y dejaba el mecanismo de proxy sin usar en
todo el corpus. Solo se omite el proxy cuyo ticker ya está cubierto de forma
directa.

### 4.10 Infraestructura

- **`pgvector/pgvector:pg15`** en lugar de `postgres:15`: el PRD §5.3 declara
  `embedding vector(1024)`, que no existe sin la extensión.
- **`postgres` no usa `user: ${UID}:${GID}`**, apartándose de la convención del
  resto de servicios del laboratorio. `initdb` falla sobre un volumen nombrado
  si se le fuerza el UID del host. La convención existe para evitar archivos
  `root` en bind mounts; aquí no hay bind mount de datos. El servicio `app` sí
  la respeta.

---

## 5. Rendimiento medido

Sobre 119 noticias, 4 008 precios y 2 000 puntos macro. Comparado con los SLA
del PRD §7.

| Operación | Medido | SLA | |
|---|---|---|---|
| Ingesta de 5 fuentes | 11 s | < 15 min | ✅ |
| Validación (Bronze → Silver) | 9,2 s | < 1 min | ✅ |
| Enriquecimiento NLP | 1,75 s/noticia → 14,6 min por 500 | < 10 min | ❌ |
| Transformación de mercado | 0,27 s | — | ✅ |
| Correlación XMEX | 0,55 s | < 5 s | ✅ |
| Construcción del índice FAISS | 0,01 s | < 30 s | ✅ |
| Búsqueda semántica Top-K | 75 ms | < 500 ms | ✅ |

### El único SLA incumplido, y por qué no es del código

El cliente envía las 8 llamadas simultáneas que exige el PRD §2.1, pero el
Ollama compartido corre con `OLLAMA_NUM_PARALLEL: 1` y las serializa. La prueba
es limpia: **1,75 s/noticia con lote 8 y exactamente 1,75 s/noticia con lote
16** — el paralelismo del cliente no cambia nada.

Elevar ese valor toca un servicio compartido con otros proyectos del laboratorio
y cada ranura paralela reserva su propia caché KV, así que requiere autorización
explícita y vigilar los 16 GB. Sin ese cambio, el SLA no es alcanzable por mucho
que se suba el lote del cliente. (ADR-13)

### Búsqueda: desglose

FAISS puro **0,84 ms** · consulta completa en caliente **75 ms** · arranque en
frío **3,9 s**, que es cargar el modelo de embeddings.

Ese arranque tiene una implicación de diseño: **el SLA solo se sostiene en un
proceso de larga duración**. Un CLI que recarga 2 GB de modelo en cada
invocación nunca lo cumplirá. Un fallo real derivado de esto: `search_semantic()`
creaba un embebedor nuevo por llamada y pagaba la carga en *cada* consulta
(1 929 ms); el `lru_cache` lo dejó en 75 ms.

### Embeddings: la medición contradijo la intuición

`EMBEDDING_BACKEND` conmuta entre dos modelos de 1024 dimensiones. La hipótesis
era que usar `bge-m3` —ya residente en el Ollama compartido— evitaría arrastrar
`torch` a la imagen sin coste. Medido:

| Backend | Latencia de consulta |
|---|---|
| `intfloat/multilingual-e5-large` (local) | **70–80 ms** |
| `bge-m3` (Ollama compartido) | 106–4 169 ms, media 2 232 |

La enorme varianza viene de que `bge-m3` compite por VRAM con el modelo de
inferencia y Ollama los intercambia. El default del PRD no solo se respeta: es
el que cumple el SLA. La alternativa queda documentada por si algún día conviene
sacar `torch` de la imagen, pero hoy costaría el SLA. (ADR-9)

---

## 6. Decisiones

Los ADR completos están en [`docs/HARNESS.md`](HARNESS.md). Resumen:

| ADR | Decisión |
|---|---|
| 1–4 | El repo es el código; GitHub es la fuente de verdad; deploy *pull-based*; secretos manuales en el servidor |
| 5 | Reusar el Ollama compartido por `host-gateway` en vez de levantar uno propio |
| 6 | Puertos solo en loopback + túnel SSH |
| 7 | Alcance por fases: arnés → scaffold → contratos → pipeline |
| 8 | `qwen3.5:9b` porque los modelos del PRD no están disponibles |
| 9 | Embeddings conmutables; medido, gana `e5-large` |
| 10 | El bypass macro exige léxico; la fuente solo refuerza |
| 11 | La configuración de fuentes se verifica contra el mundo, no contra el PRD |
| 12 | `qwen3.5` razona: hay que desactivarlo con `think: false` |
| 13 | El async×8 está implementado pero lo bloquea `OLLAMA_NUM_PARALLEL=1` |
| 14 | El repo es ejecutable por cualquiera: detección de contexto por alias SSH y perfil `standalone` de Ollama |

---

## 7. Qué falta para Fase 2

- **Corpus.** Es el límite real hoy, no el código. Recuperar El Economista y los
  Eventos Relevantes de la BMV multiplicaría la señal; con el corpus actual los
  criterios se cumplen con un caso de cada cosa.
- **Servicio de consulta de larga duración** en lugar del CLI, para que el SLA
  de búsqueda se sostenga en uso real.
- **`OLLAMA_NUM_PARALLEL`** en el servicio compartido, para que el async×8 rinda.
- **Scheduler** post-cierre (15:30 CT) que dispare el batch diario; hoy las
  etapas se invocan a mano.
- Lo que el PRD §2.2 ya sitúa fuera de Fase 1: GCP, Neo4j/GraphRAG, LLMs
  comerciales y Terraform.
