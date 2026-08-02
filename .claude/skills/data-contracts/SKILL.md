---
name: data-contracts
description: Autorar y validar los contratos de datos Pydantic de la capa Silver (SilverNews, MarketPrice, MacroIndicator) y las convenciones de silver_dead_letters — tipado estricto, integridad semántica (≥1 Ticker/Sector/Entidad), bypass macroeconómico y enrutamiento de rechazos a cuarentena. Úsalo al crear o modificar esquemas de validación.
---

# data-contracts — Contratos Pydantic + Dead Letter Queue

Define y mantiene la frontera de calidad entre Bronze (crudo) y Silver (validado). Referencia: PRD §5.2 y §6.

## Doble nivel de validación (obligatorio)

Todo registro se evalúa contra un `BaseModel` de Pydantic en dos dimensiones:

1. **Tipado estricto:** tipos, longitudes (`title` ≤ 1024, `content` ≤ 8192), formatos de fecha (ISO 8601), URLs válidas, rangos (`price > 0`, `volume ≥ 0`).
2. **Integridad semántica:** el registro debe tener **al menos un** `ticker`, `sector` o `entity`. Sin ninguno → rechazo `MISSING_ENTITY`.

## Excepción: Bypass macroeconómico

Noticias de alto impacto sobre **tasas / inflación / política monetaria** (detectadas por el LLM o por `source = bloomberg`) pueden no mencionar ticker. En ese caso:
- El validador asigna `macro_bypass = true`.
- El registro pasa a `silver_news` **aunque `tickers` esté vacío**.
- Evita falsos negativos que borrarían contexto macro crítico.

## Contratos (esquemas de referencia — PRD §5.2)

- **`SilverNews`** → `silver_news`. PK `guid` = SHA-256 de `source + url + published_at`. Campos: `title, content, url, source, published_at, ingested_at, tickers?, sector?, entities?, enriched=false, macro_bypass=false, raw_batch_uuid`.
- **`MarketPrice`** → `silver_market_prices`. Único `(ticker, date)`. OHLCV con `> 0` / `≥ 0`.
- **`MacroIndicator`** → `silver_macro_indicators`. Único `(series_id, date)`.

## Dead Letter Queue

Registros que fallan (y no aplica bypass) van a `silver_dead_letters` con:
- `raw_payload` (JSONB, el registro original completo — trazabilidad),
- `rejection_reason` ∈ {`MISSING_ENTITY`, `INVALID_URL`, `TYPE_MISMATCH`, `DUPLICATE_KEY`, …} (motivo específico, no genérico),
- `batch_uuid` (FK → Bronze), `rejected_at`.

Nunca descartes silenciosamente un registro inválido: **siempre** a cuarentena con motivo.

## Reglas de autoría

- Un solo lugar de verdad por contrato (un módulo Pydantic por entidad); las etapas del pipeline lo importan, no lo redefinen.
- Validación **en memoria**, antes de escribir a PostgreSQL (el cuello de botella es el parsing; mantén Pydantic rápido).
- Los motivos de rechazo son un `enum`/constantes compartidas — no strings ad hoc dispersos.
- Cambiar un contrato es un cambio de esquema: acompáñalo de migración SQL y actualiza **acceptance-verify** si toca un criterio de aceptación.
- Mantén el mapeo sector→proxy (PRD §3.3) como dato de configuración versionado, no hardcodeado en la lógica de validación.

## Verificación

- Al menos 1 noticia sin entidad debe aparecer en `silver_dead_letters` con `MISSING_ENTITY`.
- Al menos 1 noticia macro sin ticker debe pasar a `silver_news` con `macro_bypass = true`.
- Duplicados: `SELECT guid, COUNT(*) FROM silver_news GROUP BY guid HAVING COUNT(*) > 1` ⇒ 0 filas.
Estos checks los ejecuta el skill **acceptance-verify**.
