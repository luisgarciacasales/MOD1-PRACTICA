#!/usr/bin/env bash
#
# Una corrida programada. Lo invoca cron; no se lanza a mano salvo para probar.
#
#   bash scripts/scheduler.sh            corrida normal
#   bash scripts/scheduler.sh --arranque tras encender el equipo: corre solo si
#                                        hoy falta alguna franja ya vencida
#
# POR QUÉ EXISTE, con el número que lo justifica: el corpus de noticias no se
# compra, se acumula. Medido el 10-sep-2026, un año de corridas rinde quince
# veces más señal que ocho años de archivo de prensa (ADR-20), así que **cada
# día sin corrida es pérdida irrecuperable** — el feed de El Financiero solo
# conserva las últimas 100 entradas, unas 20 horas.
#
# TRES FRANJAS, lunes a viernes: 08:00, 15:30 y 20:00 hora de Ciudad de México.
# Ninguna cae con el mercado abierto (8:30-15:00): una corrida a media sesión
# archivaría en Bronze un precio intradía como si fuera el cierre, y si la
# siguiente fallara quedaría ahí sin que nadie lo note.
#
# EN DÍA INHÁBIL corre igual, pero solo noticias. No hay precios que pedir y sí
# medios publicando; saltarlo del todo costaría unas 36 horas de hueco por cada
# inhábil, y son diez al año.
#
# EL EQUIPO NO ESTÁ 24/7 —se enciende entre las 5:42 y las 8:06 y se apaga
# entre las 18:00 y las 23:00, medido sobre el historial de arranques— así que
# `cron` por sí solo perdería la franja de las 08:00 los días que se encienda
# más tarde. De ahí `--arranque`.

set -uo pipefail

PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROYECTO"

MARCAS="$PROYECTO/data/logs/scheduler"
LOG="$PROYECTO/data/logs/scheduler.log"
mkdir -p "$MARCAS"

HOY=$(date +%F)
AHORA=$(date +%H%M)

anotar() { echo "$(date '+%F %H:%M:%S') $*" >> "$LOG"; }

# Qué franja corresponde a la hora actual. Se usa para no repetir una corrida
# ya hecha: el `--arranque` puede coincidir con la hora de cron.
franja_actual() {
    if   (( 10#$AHORA >= 2000 )); then echo "2000"
    elif (( 10#$AHORA >= 1530 )); then echo "1530"
    elif (( 10#$AHORA >= 0800 )); then echo "0800"
    else echo ""; fi
}

FRANJA=$(franja_actual)
if [[ -z "$FRANJA" ]]; then
    anotar "[scheduler] antes de las 08:00 — nada que hacer"
    exit 0
fi

# Sábados y domingos no: no hay mercado y el volumen de prensa financiera cae
# tanto que el feed cubre de sobra el fin de semana entero.
DIA_SEMANA=$(date +%u)
if (( DIA_SEMANA > 5 )); then
    anotar "[scheduler] fin de semana — no se corre"
    exit 0
fi

MARCA="$MARCAS/${HOY}_${FRANJA}"
if [[ -f "$MARCA" ]]; then
    anotar "[scheduler] franja $FRANJA de hoy ya corrida — se omite"
    exit 0
fi

# En modo arranque solo interesa recuperar lo perdido, no adelantar trabajo.
if [[ "${1:-}" == "--arranque" ]]; then
    anotar "[scheduler] arranque: recuperando la franja $FRANJA"
fi

# Si otra corrida sigue viva —una tanda de enrich larga, un backfill— no se
# encabalga: se anota y se deja para la siguiente franja.
if docker top mod1-app 2>/dev/null | grep -qE "batch\.py|pipeline\.(enrich|ingest)"; then
    anotar "[scheduler] hay una corrida en curso — se omite la franja $FRANJA"
    exit 0
fi

anotar "[scheduler] franja $FRANJA — arrancando"
docker compose exec -T app python scripts/batch.py >> "$LOG" 2>&1
CODIGO=$?

if (( CODIGO == 0 )); then
    touch "$MARCA"
    anotar "[scheduler] franja $FRANJA — OK"
    # La copia solo tras la última franja del día: hacerla tres veces no añade
    # protección y sí triplica el árbol de snapshots.
    if [[ "$FRANJA" == "2000" ]]; then
        bash scripts/backup.sh >> "$LOG" 2>&1 && anotar "[scheduler] copia del día hecha"
    fi
else
    anotar "[scheduler] franja $FRANJA — FALLÓ (código $CODIGO), se reintentará en la siguiente"
    # Un fallo es lo único que justifica interrumpir a alguien. Las últimas
    # líneas del log bastan para saber qué etapa cayó sin abrir el servidor.
    docker compose exec -T app python -m src.pipeline.avisos         --probar --urgente         --titulo "Corrida de las ${FRANJA:0:2}:${FRANJA:2} falló"         --cuerpo "$(tail -12 "$LOG")" >> /dev/null 2>&1 || true
fi

# Y la calidad de los datos, una vez al día: el pipeline puede terminar en
# verde con datos que no lo están, que es justo lo que `calidad` vigila.
if [[ "$FRANJA" == "2000" ]] && (( CODIGO == 0 )); then
    SALIDA_CALIDAD=$(docker compose exec -T app python -m src.pipeline.calidad 2>&1)
    if ! grep -q "0 PROBLEMA" <<< "$SALIDA_CALIDAD"; then
        anotar "[scheduler] calidad reporta PROBLEMA"
        docker compose exec -T app python -m src.pipeline.avisos             --probar --urgente --titulo "Calidad de datos: PROBLEMA"             --cuerpo "$(grep -E "PROBLEMA" <<< "$SALIDA_CALIDAD" | head -4)"             >> /dev/null 2>&1 || true
    fi
fi

# Las marcas viejas no sirven de nada y ensucian: se conservan dos semanas.
find "$MARCAS" -type f -mtime +14 -delete 2>/dev/null

exit $CODIGO
