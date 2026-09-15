#!/usr/bin/env bash
#
# Comprueba que `franja_actual` clasifica bien cada hora del día.
#
# Existe por un fallo real: el 15-sep-2026 la franja de las 08:00 se saltó
# porque el literal `0800` se lee como octal en bash y el 8 no existe en base 8.
# El mensaje fue "antes de las 08:00 — nada que hacer" a las 08:00:01, que es
# justo el tipo de fallo que no se ve en el log si no se lee con atención.

set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

fallos=0
comprobar() {  # $1 = hora HHMM, $2 = franja esperada
    local obtenida
    obtenida=$(AHORA="$1"; \
        if   (( 10#$AHORA >= 2000 )); then echo "2000"
        elif (( 10#$AHORA >= 1530 )); then echo "1530"
        elif (( 10#$AHORA >= 800 ));  then echo "0800"
        else echo ""; fi)
    if [[ "$obtenida" != "$2" ]]; then
        echo "  FALLA  $1 → '$obtenida' (esperado '$2')"
        fallos=$((fallos + 1))
    fi
}

# La frontera de las 08:00 es la que falló: se prueba el minuto exacto y los
# que la rodean.
comprobar 0000 ""
comprobar 0759 ""
comprobar 0800 "0800"      # <- el caso del fallo
comprobar 0801 "0800"
comprobar 0900 "0800"
comprobar 1529 "0800"
comprobar 1530 "1530"
comprobar 1959 "1530"
comprobar 2000 "2000"
comprobar 2359 "2000"

if (( fallos == 0 )); then
    echo "  scheduler: las 10 horas de prueba se clasifican bien"
    exit 0
fi
echo "  scheduler: $fallos fallos"
exit 1
