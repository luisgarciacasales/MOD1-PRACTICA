# Evidencia de ejecución de punta a punta

**Generado:** 2026-08-02T07:27:27+00:00  
**Modelo NLP:** `qwen3.5:9b` · **Embeddings:** `intfloat/multilingual-e5-large` (1024 dim)  
**Lotes async:** 8 · **Calendario:** XMEX

## 1. Bronze — lotes crudos, con timestamp, sin transformar

| Fuente | Lotes | Registros | Último timestamp | Checksum SHA-256 |
|---|---|---|---|---|
| banxico | 2 | 4000 | 2026-08-02T05:49:39 | 1c3fb494132162f2… |
| bloomberg | 4 | 400 | 2026-08-02T06:20:56 | 140fbb72abf4e207… |
| financiero | 5 | 500 | 2026-08-02T06:20:57 | b5edde355728a2d9… |
| finnovista | 4 | 80 | 2026-08-02T05:49:37 | c98a7f708110f347… |
| yahoo_finance | 3 | 12027 | 2026-08-02T05:49:48 | ce0ab42ec28bcbfe… |

**18 lotes** · 18/18 con checksum íntegro · 18/18 en modo solo lectura (0444), lo que impide cualquier transformación posterior sobre Bronze.

## 2. Ejecución de punta a punta, dos veces sobre el mismo lote

No se reingiere entre pasadas: se reprocesan **exactamente los mismos lotes de Bronze**. Si se reingiriera, los feeds habrían cambiado y las filas nuevas de la segunda pasada no distinguirían «hay noticias nuevas» de «el UPSERT no funciona».

Silver y Gold se vaciaron antes de la pasada 1, de modo que la tabla siguiente muestra la carga completa y su reproceso. **Bronze no se toca**: es inmutable y basta por sí solo para reconstruir todo lo demás.

```
### Pasada 1

    validate    OK              9.2 s
    enrich      OK            202.2 s
    transform   OK              0.2 s
    correlate   OK              0.5 s
    index       OK             38.2 s

### Pasada 2

    validate    OK              9.6 s
    enrich      OK              0.2 s
    transform   OK              0.2 s
    correlate   OK              0.5 s
    index       OK              0.2 s

```

Tiempo total: pasada 1 = 250.3 s · pasada 2 = 10.7 s

## 3. Idempotencia — el reproceso da `filas_nuevas = 0`

| Tabla | Antes | Tras pasada 1 | Tras pasada 2 | filas_nuevas (2ª) |
|---|---|---|---|---|
| `silver_news` | 0 | 119 | 119 | **0** |
| `silver_market_prices` | 0 | 4008 | 4008 | **0** |
| `silver_macro_indicators` | 0 | 2000 | 2000 | **0** |
| `gold_enriched_news` | 0 | 119 | 119 | **0** |
| `gold_market_prices` | 0 | 4008 | 4008 | **0** |
| `gold_macro_indicators` | 0 | 2000 | 2000 | **0** |
| `gold_news_market_corr` | 0 | 5 | 5 | **0** |

**Resultado: CUMPLE** — la pasada 1 cargó 12259 filas y la pasada 2 añadió **0** reprocesando los mismos datos.

`enrich` solo procesa noticias con `enriched = false`, así que en la segunda pasada no tiene trabajo y termina en décimas de segundo: eso *es* su comportamiento idempotente, no una etapa saltada.

## 4. Duplicados — `COUNT(*) > 1` por clave natural = 0 filas

| Tabla | Clave natural | Filas | Grupos duplicados |
|---|---|---|---|
| `silver_news` | `guid` | 119 | **0** |
| `silver_market_prices` | `ticker, date` | 4008 | **0** |
| `silver_macro_indicators` | `series_id, date` | 2000 | **0** |
| `gold_enriched_news` | `guid` | 119 | **0** |
| `gold_market_prices` | `ticker, date` | 4008 | **0** |
| `gold_macro_indicators` | `series_id, date` | 2000 | **0** |
| `gold_news_market_corr` | `news_guid, ticker, price_date` | 5 | **0** |

**Resultado: CUMPLE** — la consulta del criterio devuelve 0 filas en las 7 tablas con clave natural.

## 5. Contrato — validación explícita y cuarentena con motivo

| Motivo de rechazo | Registros en cuarentena |
|---|---|
| `MISSING_ENTITY` | 1104 |
| `TYPE_MISMATCH` | 12 |

Ejemplos de registros en cuarentena con su motivo:

| Fuente | Detalle | Título original |
|---|---|---|
| bloomberg | : Value error, MISSING_ENTITY | Meta se hunde y Microsoft se dispara: cinco  |
| bloomberg | : Value error, MISSING_ENTITY | Meta, Microsoft y la matriz de Google: los p |

El payload original se conserva íntegro en `raw_payload` (JSONB) para poder revisarlo o reprocesarlo. **80 noticias** entraron por el bypass macroeconómico, la única excepción admitida a la regla de «al menos un Ticker, Sector o Entidad».

## 6. Gold — índice vectorial funcional y consulta semántica

Índice `IndexFlatIP` (producto interno sobre vectores normalizados = similitud coseno) con **119 vectores de 1024 dimensiones**, persistido en `/app/data/faiss/index.index` (477 KB).

**Consulta:** «recorte de la tasa de interés de Banxico y su efecto en la banca» — 5 resultados en **78 ms**

| # | Score | Titular | Fuente | Fecha | Sentimiento |
|---|---|---|---|---|---|
| 1 | 0.832 | Ganancias de CFE se desploman por el dólar débil | bloomberg | 2026-07-30 | negative |
| 2 | 0.829 | ¿Invertir el día de la reunión de la Fed? Así suelen reacc | bloomberg | 2026-07-29 | neutral |
| 3 | 0.829 | S&P 500 cierra al alza tras dato de inflación, mientras IB | bloomberg | 2026-07-14 | neutral |
| 4 | 0.820 | Fed, Meta y Microsoft: el miércoles que definirá el rumbo  | bloomberg | 2026-07-29 | neutral |
| 5 | 0.819 | Ignacio Deschamps deja el rol de presidente del Consejo de | bloomberg | 2026-07-29 | neutral |

**Consulta:** «competencia de fintechs y neobancos contra la banca tradicional» — 5 resultados en **77 ms**

| # | Score | Titular | Fuente | Fecha | Sentimiento |
|---|---|---|---|---|---|
| 1 | 0.815 | Un crítico de la IA advierte que OpenAI podría ser “el Leh | bloomberg | 2026-07-16 | negative |
| 2 | 0.814 | La carta con la que Jensen Huang, CEO de Nvidia, debutó en | bloomberg | 2026-07-24 | neutral |
| 3 | 0.804 | Exclusiva: Se reactiva el mercado de deuda privada tras el | bloomberg | 2026-07-24 | positive |
| 4 | 0.801 | BofA alerta que cada vez es más difícil encontrar monedas  | bloomberg | 2026-07-31 | neutral |
| 5 | 0.801 | Moonshot sacude a Wall Street: qué es Kimi K3 y por qué re | bloomberg | 2026-07-17 | negative |

**Consulta:** «resultados y utilidades de una emisora mexicana» — 5 resultados en **72 ms**

| # | Score | Titular | Fuente | Fecha | Sentimiento |
|---|---|---|---|---|---|
| 1 | 0.834 | Ganancias de CFE se desploman por el dólar débil | bloomberg | 2026-07-30 | negative |
| 2 | 0.830 | Impuestos y costo de la deuda de Pemex presionan ganancias | bloomberg | 2026-07-31 | neutral |
| 3 | 0.807 | Fibra de CFE financiará inversiones en redes eléctricas ha | bloomberg | 2026-07-29 | neutral |
| 4 | 0.798 | IPO de YPF Luz como “caso testigo” del retorno argentino a | bloomberg | 2026-07-14 | neutral |
| 5 | 0.795 | Las árbitras de la verdad y los periodistas | financiero | 2026-07-31 | neutral |

Arranque en frío (carga del modelo de embeddings): 4006 ms. No es latencia de búsqueda — en un proceso de larga duración se paga una sola vez.

## Resumen de criterios

| Criterio | Mínimo exigido | Resultado | Evidencia |
|---|---|---|---|
| Bronze | 2+ lotes crudos, timestamp, sin transformar | CUMPLE | 18 lotes, 18 con checksum íntegro, 18 inmutables |
| Contrato | validación explícita, cuarentena con motivo | CUMPLE | 1116 registros en cuarentena, 2 motivos tipados distintos |
| Idempotencia | reproceso da filas_nuevas = 0 | CUMPLE | segunda pasada: +0 filas en 7 tablas |
| Duplicados | COUNT(*) > 1 por clave natural = 0 filas | CUMPLE | 0 grupos duplicados en 7 tablas |
| Gold | índice vectorial funcional, 1+ consulta semántica | CUMPLE | 119 vectores indexados, 3 consultas demostradas, 119 embeddings en pgvector |

