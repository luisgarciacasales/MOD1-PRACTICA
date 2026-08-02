---
name: acceptance-verify
description: Convierte la Definición de Terminado del PRD (§8) en checks ejecutables sobre el sistema desplegado en mi-pc — Bronze 5 fuentes, contrato semántico, idempotencia, 0 duplicados, NLP/proxy, calendario XMEX y consulta semántica FAISS. Úsalo para validar que una etapa cumple los criterios de aceptación antes de darla por terminada.
---

# acceptance-verify — Definición de Terminado ejecutable

Cada criterio del PRD §8 se traduce a un check reproducible. Ejecuta vía **remote-ops**/**deploy** contra el stack en `mi-pc`. Reporta cada check como PASS/FAIL con su salida real; **no declares terminado nada que no haya pasado su check**.

> Los nombres de tablas/módulos siguen `medallion-pipeline` y `data-contracts`. Ajusta `<user>`/`<db>` a los del `.env` del servidor.

## Checklist

### 1. Bronze — 5 fuentes, 2+ lotes, inmutable
```bash
ssh mi-pc 'ls -R ~/augmented/services/MOD1-PRACTICA/data/bronze/ | head -60'
```
PASS si existen las 5 fuentes (bmv_eventos, financiero, economista, bloomberg, yahoo_finance, banxico), ≥2 lotes con `metadata.json` (batch_uuid + checksum) y datos sin transformar.

### 2. Contrato semántico + bypass macro
```sql
SELECT rejection_reason, COUNT(*) FROM silver_dead_letters GROUP BY 1;         -- debe incluir MISSING_ENTITY
SELECT COUNT(*) FROM silver_news WHERE macro_bypass = true;                    -- ≥ 1
```
PASS si hay ≥1 rechazo `MISSING_ENTITY` y ≥1 noticia macro con `macro_bypass = true`.

### 3. Idempotencia
Reprocesa el mismo batch y confirma `filas_nuevas = 0` en `silver_news`, `silver_market_prices`, `silver_macro_indicators` (comparar `COUNT(*)` antes/después del reproceso).

### 4. Cero duplicados
```sql
SELECT guid, COUNT(*) FROM silver_news GROUP BY guid HAVING COUNT(*) > 1;      -- 0 filas
```

### 5. NLP enrichment (+ proxy + async×8)
```sql
SELECT COUNT(*) FROM gold_enriched_news WHERE ner_tickers IS NOT NULL;                 -- NER funcional
SELECT sentiment_label, COUNT(*) FROM gold_enriched_news GROUP BY 1;                    -- 3 clases posibles
SELECT COUNT(*) FROM gold_enriched_news WHERE is_ma_event = true;                       -- ≥ 1
SELECT COUNT(*) FROM gold_news_market_corr WHERE is_proxy = true;                       -- ≥ 1 proxy
```
PASS si: NER extrae tickers/entidades en español, sentimiento asignado, ≥1 evento M&A, fintech tagging activo, ≥1 caso `is_proxy = true`, y el enriquecimiento corrió en lotes async de 8 (verificar en logs del pipeline).

### 6. Correlación temporal XMEX
```sql
-- Una noticia de viernes debe tener price_date = siguiente día hábil (lunes o posterior).
SELECT news_guid, news_date, next_trading_day, price_date
FROM gold_news_market_corr
WHERE EXTRACT(DOW FROM news_date) = 5      -- viernes
LIMIT 5;
```
PASS si `price_date` es el siguiente día hábil bursátil (no el fin de semana). Demostrable con ≥1 caso.

### 7. Gold — FAISS + correlación
- Ejecutar `search_semantic("<consulta en español>", k=10)` y obtener resultados relevantes (< 500 ms).
- ≥1 consulta que relacione sentimiento negativo de una noticia con caída de precio del siguiente día hábil del ticker.

## Reporte

Presenta una tabla `Criterio | Check | PASS/FAIL | Evidencia`. Si algo falla, incluye la salida y remite a la etapa responsable en **medallion-pipeline** / **data-contracts**. No maquilles resultados.
