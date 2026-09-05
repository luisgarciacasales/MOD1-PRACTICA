#!/usr/bin/env bash
#
# ¿Cuánto varía la inferencia entre dos corridas idénticas, y depende de la
# concurrencia?
#
#   bash scripts/experimento_determinismo.sh [n_noticias]
#
# Se ejecuta en el SERVIDOR. Usa la GPU: comprueba antes que esté libre, porque
# `lab-ollama` es compartido con los demás servicios del laboratorio.
#
# POR QUÉ. El 4-sep-2026 se reprocesaron las 1.656 noticias del corpus con el
# mismo modelo y el mismo prompt, y 302 filas (18,2%) salieron distintas —
# `sentiment_score` con un delta medio de 0,251 y 77 vuelcos de etiqueta (ADR-18).
# La hipótesis es que la causa es el batching dinámico en GPU: con varias
# peticiones concurrentes, llama.cpp forma lotes de tamaño y orden variables, y
# como las reducciones en punto flotante no son asociativas, los logits difieren
# en los últimos bits y un empate cercano se resuelve distinto.
#
# El experimento la pone a prueba: dos corridas con concurrencia 8 y dos con
# concurrencia 1, sobre las MISMAS noticias (`_SQL_PENDIENTES` ordena por
# `published_at DESC`, así que con el mismo límite selecciona el mismo conjunto).
# Si la hipótesis es cierta, la tasa de cambio debe caer a cero con concurrencia
# 1 y mantenerse con 8. Si no cae, la causa está en otro sitio y hay que buscarla.

set -euo pipefail

N="${1:-50}"
cd "$(dirname "${BASH_SOURCE[0]}")/.."
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

USO=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader | tr -d ' %')
if (( USO > 15 )); then
    echo "[exp] la GPU está al ${USO}% — hay otro trabajo en curso." >&2
    echo "[exp] lab-ollama es compartido; espera a que se libere." >&2
    exit 1
fi

volcar() {
    docker compose exec -T postgres psql -U mod1 -d mod1_practica -tA -c "
        SELECT g.guid||E'\t'||g.ner_tickers::text||E'\t'||coalesce(g.sentiment_label,'-')
               ||E'\t'||coalesce(round(g.sentiment_score::numeric,3)::text,'-')
               ||E'\t'||g.is_ma_event::text||E'\t'||g.ma_event_type
        FROM gold_enriched_news g
        JOIN (SELECT guid FROM silver_news ORDER BY published_at DESC LIMIT $N) s
          ON s.guid = g.guid
        ORDER BY g.guid;" > "$1"
}

corrida() {  # $1 = concurrencia, $2 = archivo de salida
    docker compose exec -T app python -m src.pipeline.enrich \
        --reprocess --limit "$N" --batch-size "$1" > /dev/null 2>&1
    volcar "$2"
}

comparar() {  # $1 $2 = archivos ; imprime "cambiadas/total"
    local n; n=$(diff <(sort "$1") <(sort "$2") | grep -c '^<' || true)
    echo "$n/$(wc -l < "$1" | tr -d ' ')"
}

echo "[exp] $N noticias · cuatro corridas (8, 8, 1, 1)"
for c in 8 1; do
    corrida "$c" "$TMP/${c}_a"
    corrida "$c" "$TMP/${c}_b"
    printf '  concurrencia %-2s → %s filas cambiadas entre dos corridas idénticas\n' \
        "$c" "$(comparar "$TMP/${c}_a" "$TMP/${c}_b")"
done

echo "[exp] si con 1 baja a 0 y con 8 no, la causa es el batching (ADR-18)."
