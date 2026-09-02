#!/usr/bin/env bash
#
# ¿Qué está pasando ahora mismo? Una foto del sistema en una pantalla.
#
#   make estado
#
# Existe porque el 1-sep-2026 se dio por terminada una corrida DOS veces
# mientras seguía ejecutándose: se sondeó con `ps` (que no está instalado en el
# contenedor de la aplicación) y con un nombre de contenedor equivocado. Cuando
# no hay una forma canónica de preguntar, cada vez se improvisa una distinta y
# alguna sale mal. Lo fiable desde el host es `docker top mod1-app`.
#
# Solo LEE. No arranca, no para y no escribe nada.

set -uo pipefail

PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROYECTO"

APP="$(docker compose ps --format '{{.Name}}' 2>/dev/null | grep -- '-app$' | head -1)"
APP="${APP:-mod1-app}"
COPIAS="${BACKUP_DIR:-$HOME/augmented/backups/MOD1-PRACTICA}"

psql_() { docker compose exec -T postgres psql -U "${POSTGRES_USER:-mod1}" \
              -d "${POSTGRES_DB:-mod1_practica}" -tA -c "$1" 2>/dev/null; }

printf '\n\033[1mEstado de MOD1-PRACTICA\033[0m — %s (hora de mercado)\n' "$(date '+%Y-%m-%d %H:%M %Z')"
printf '%s\n' "$(printf '=%.0s' {1..78})"

# --- ¿Corre algo? ----------------------------------------------------------
# `docker top` pregunta al demonio desde el host: no depende de que el
# contenedor tenga `ps`, que es justo donde falló el sondeo improvisado.
EN_CURSO="$(docker top "$APP" 2>/dev/null | grep -oP 'src\.pipeline\.\K\w+|scripts/\K[\w.]+' | paste -sd' ' -)"
if [[ -n "$EN_CURSO" ]]; then
    printf '  %-16s \033[33mEN CURSO\033[0m · %s\n' "AHORA MISMO" "$EN_CURSO"
else
    printf '  %-16s en reposo\n' "AHORA MISMO"
fi

# --- Última corrida --------------------------------------------------------
ULTIMA="$(tail -1 data/logs/historial.log 2>/dev/null)"
if [[ -n "$ULTIMA" ]]; then
    CUANDO="${ULTIMA%% *}"
    RESUMEN="$(echo "$ULTIMA" | sed 's/^[^ ]* *//' | cut -c1-58)"
    TRANSCURRIDO=$(( ( $(date +%s) - $(date -d "$CUANDO" +%s 2>/dev/null || echo 0) ) / 3600 ))
    printf '  %-16s %s · hace %sh\n' "ÚLTIMA CORRIDA" "$(date -d "$CUANDO" '+%Y-%m-%d %H:%M')" "$TRANSCURRIDO"
    printf '  %-16s %s\n' "" "$RESUMEN"
else
    printf '  %-16s sin registro en data/logs/historial.log\n' "ÚLTIMA CORRIDA"
fi

# --- Servicios -------------------------------------------------------------
SERVICIOS="$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null | awk '{printf "%s(%s) ", $1, $2}')"
curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
    && OLLAMA="lab-ollama(responde)" || OLLAMA=$'\033[31mlab-ollama(NO responde)\033[0m'
printf '  %-16s %s%s\n' "SERVICIOS" "$SERVICIOS" "$OLLAMA"

# --- Bronze: lo que falta por validar --------------------------------------
# El número que de verdad importa: un lote escrito y no validado es dato que
# está en disco pero todavía no existe para nadie.
EN_DISCO=$(find data/bronze -mindepth 4 -maxdepth 4 -type d 2>/dev/null | wc -l)
PROCESADOS=$(psql_ "SELECT COUNT(*) FROM bronze_lotes_procesados;")
HOY=$(ls -d data/bronze/*/*/"$(date +%F)" 2>/dev/null | wc -l)
PENDIENTES=$(( EN_DISCO - ${PROCESADOS:-0} ))
if (( PENDIENTES > 0 )); then
    printf '  %-16s %s lotes · %s de hoy · \033[33m%s sin validar\033[0m\n' "BRONZE" "$EN_DISCO" "$HOY" "$PENDIENTES"
else
    printf '  %-16s %s lotes · %s de hoy · todos validados\n' "BRONZE" "$EN_DISCO" "$HOY"
fi

# --- Volumen de datos ------------------------------------------------------
printf '  %-16s noticias %s · precios %s · fundamentales %s · valuación %s\n' "DATOS" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_news;')" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_market_prices;')" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_fundamentals;')" \
    "$(psql_ 'SELECT COUNT(*) FROM gold_valuation;')"
printf '  %-16s %s\n' "ÚLTIMO GOLD" \
    "$(psql_ "SELECT to_char(MAX(ingested_at AT TIME ZONE 'America/Mexico_City'), 'YYYY-MM-DD HH24:MI') FROM gold_valuation;")"

# --- Copias ----------------------------------------------------------------
RECIENTE="$(find "$COPIAS" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort | tail -1)"
if [[ -n "$RECIENTE" ]]; then
    DIAS=$(( ( $(date +%s) - $(stat -c %Y "$RECIENTE") ) / 86400 ))
    printf '  %-16s %s · hace %s día(s) · %s en total\n' "ÚLTIMA COPIA" \
        "$(basename "$RECIENTE")" "$DIAS" "$(du -sh "$COPIAS" 2>/dev/null | cut -f1)"
else
    printf '  %-16s \033[31mNINGUNA\033[0m — corre `make backup`\n' "ÚLTIMA COPIA"
fi

printf '\n  Para el detalle: `make historial`, `make verify`, `make calidad`\n\n'
