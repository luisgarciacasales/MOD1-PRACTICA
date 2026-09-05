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
    printf '  %-16s %s · hace %sh\n' "CORRIDA PREVIA" "$(date -d "$CUANDO" '+%Y-%m-%d %H:%M')" "$TRANSCURRIDO"
    printf '  %-16s %s\n' "" "$RESUMEN"
else
    printf '  %-16s sin registro en data/logs/historial.log\n' "CORRIDA PREVIA"
fi

# --- Servicios -------------------------------------------------------------
SERVICIOS="$(docker compose ps --format '{{.Name}} {{.State}}' 2>/dev/null | awk '{printf "%s(%s) ", $1, $2}')"
curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
    && OLLAMA="lab-ollama(responde)" || OLLAMA=$'\033[31mlab-ollama(NO responde)\033[0m'
printf '  %-16s %s%s\n' "SERVICIOS" "$SERVICIOS" "$OLLAMA"

# --- Bronze: lo que falta por validar --------------------------------------
# El número que de verdad importa: un lote escrito y no validado es dato que
# está en disco pero todavía no existe para nadie.
#
# Se pregunta con el MISMO código que usa `validate`, no restando conteos. La
# primera versión hacía `lotes_en_disco - filas_en_la_tabla` y decía "31 sin
# validar" con todo al día: `bronze_lotes_procesados` nació en la migración 016
# y los lotes anteriores nunca se registraron, así que la resta contaba como
# pendiente medio historial. Un panel que miente es peor que no tenerlo.
# Tres detalles que hay que copiar de validate o el número sale mal:
#   · el batch_uuid del metadata es TEXTO y `lotes_procesados` devuelve UUID;
#   · `finnovista` se salta antes de marcarse —se recarga siempre, por los
#     nombres que aporta— así que contaría como pendiente eternamente;
#   · la categoría `inferencia` NO la consume validate. Esos lotes van a Gold
#     por `enrich --desde-bronze`, así que llamarlos "sin validar" es un aviso
#     que no se puede atender nunca. Se cuentan aparte, que además es
#     información útil: dice cuánta inferencia hay respaldada en Bronze.
LEIDO=$(docker compose exec -T app python -c "
from pathlib import Path
from uuid import UUID
from src.config import get_settings
from src.pipeline import db
from src.pipeline.bronze import leer_metadata, listar_lotes
lotes = [(l, leer_metadata(l)) for l in listar_lotes(Path(get_settings().bronze_path))]
with db.conectar() as cx, cx.cursor() as cur:
    vistos = db.lotes_procesados(cur)
inferencia = [m for _, m in lotes if m['categoria'] == 'inferencia']
pendientes = [l for l, m in lotes
              if m['categoria'] != 'inferencia'
              and m['source'] != 'finnovista'
              and UUID(m['batch_uuid']) not in vistos]
print(f\"{len(lotes)} {len(pendientes)} {len(inferencia)} {sum(m['record_count'] for m in inferencia)}\")
" 2>/dev/null | tail -1)
read -r EN_DISCO PENDIENTES INF_LOTES INF_REGS <<< "$LEIDO"
HOY=$(ls -d data/bronze/*/*/"$(date +%F)" 2>/dev/null | wc -l)
if [[ -n "$PENDIENTES" ]] && (( PENDIENTES > 0 )); then
    printf '  %-16s %s lotes · %s de hoy · \033[33m%s sin validar\033[0m\n' "BRONZE" "$EN_DISCO" "$HOY" "$PENDIENTES"
else
    printf '  %-16s %s lotes · %s de hoy · todos validados\n' "BRONZE" "$EN_DISCO" "$HOY"
fi

if [[ -n "${INF_LOTES:-}" ]] && (( INF_LOTES > 0 )); then
    printf '  %-16s %s lotes · %s inferencias respaldadas · las consume `enrich --desde-bronze`\n' \
        "INFERENCIA" "$INF_LOTES" "$INF_REGS"
fi

# --- Volumen de datos ------------------------------------------------------
printf '  %-16s noticias %s · precios %s · fundamentales %s · valuación %s\n' "DATOS" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_news;')" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_market_prices;')" \
    "$(psql_ 'SELECT COUNT(*) FROM silver_fundamentals;')" \
    "$(psql_ 'SELECT COUNT(*) FROM gold_valuation;')"
printf '  %-16s %s\n' "GOLD ESCRITO" \
    "$(psql_ "SELECT to_char(MAX(ingested_at AT TIME ZONE 'America/Mexico_City'), 'YYYY-MM-DD HH24:MI') FROM gold_valuation;")"

# --- Copias ----------------------------------------------------------------
RECIENTE="$(find "$COPIAS" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort | tail -1)"
if [[ -n "$RECIENTE" ]]; then
    DIAS=$(( ( $(date +%s) - $(stat -c %Y "$RECIENTE") ) / 86400 ))
    printf '  %-16s %s · hace %s día(s) · %s en total\n' "COPIA RECIENTE" \
        "$(basename "$RECIENTE")" "$DIAS" "$(du -sh "$COPIAS" 2>/dev/null | cut -f1)"
else
    printf '  %-16s \033[31mNINGUNA\033[0m — corre `make backup`\n' "COPIA RECIENTE"
fi

printf '\n  Para el detalle: `make historial`, `make verify`, `make calidad`\n\n'
