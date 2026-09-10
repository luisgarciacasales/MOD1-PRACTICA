#!/usr/bin/env bash
#
# Espera a que termine el recorrido del archivo y repara lo que falte.
#
#   nohup setsid bash scripts/reparar_hemeroteca.sh eleconomista 2018-01 2025-12 &
#
# Corre en el HOST porque necesita `docker top` para saber si el recorrido
# sigue vivo: dentro del contenedor no hay `ps` ni `pkill`.
#
# POR QUÉ UNA SEGUNDA PASADA BASTA. `backfill_noticias` salta los meses que ya
# tienen lote en Bronze, así que relanzar el mismo rango solo trabaja sobre los
# ausentes. No hay que leer el log ni llevar una lista de fallos: el estado real
# está en Bronze y es él quien decide. Cubre los meses que el índice del Archive
# rechazó tras tres reintentos, los que quedaron a medias por un reinicio, y
# cualquier otro hueco que no hayamos previsto.
#
# Dos pasadas y para. Si un mes falla dos veces seguidas ya no es un fallo
# transitorio, y seguir insistiendo solo cargaría un servicio público gratuito
# sin resolver nada. Lo que quede se ve en el log y se decide a mano.

set -uo pipefail

MEDIO="${1:-eleconomista}"
DESDE="${2:-2018-01}"
HASTA="${3:-2025-12}"
PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$PROYECTO/data/logs/hemeroteca.log"
ESPERA_MAX=$((60 * 60 * 60))   # 60 horas: el recorrido son ~50

cd "$PROYECTO"

esperar() {
    local t=0
    while docker top mod1-app 2>/dev/null | grep -q "backfill_noticias"; do
        sleep 300
        t=$((t + 300))
        if (( t > ESPERA_MAX )); then
            echo "[reparar] el recorrido sigue vivo tras 60 h — no se repara nada" >> "$LOG"
            return 1
        fi
    done
    return 0
}

{
    echo ""
    echo "[reparar] esperando a que termine el recorrido…"
} >> "$LOG"

esperar || exit 1

{
    echo "[reparar] recorrido terminado. Segunda pasada sobre $DESDE → $HASTA:"
    echo "[reparar] solo trabajará los meses SIN lote en Bronze."
} >> "$LOG"

docker compose exec -T app python -m src.pipeline.backfill_noticias \
    --medio "$MEDIO" --desde "$DESDE" --hasta "$HASTA" >> "$LOG" 2>&1

{
    echo "[reparar] segunda pasada terminada."
    echo "[reparar] meses con lote en Bronze: $(ls -d "$PROYECTO"/data/bronze/news/${MEDIO}_archivo/*/ 2>/dev/null | wc -l)"
} >> "$LOG"
