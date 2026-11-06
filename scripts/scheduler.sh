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
# OJO con los ceros a la izquierda: bash interpreta `0800` como OCTAL y el 8 no
# existe en base 8, así que la comparación revienta con "value too great for
# base" y el `if` se va por el `else`. El 15-sep-2026 eso hizo que la franja de
# las 08:00 se saltara con el mensaje "antes de las 08:00 — nada que hacer" a
# las 08:00:01. La variable lleva `10#` para forzar decimal; los LITERALES se
# escriben sin cero delante.
franja_actual() {
    if   (( 10#$AHORA >= 2000 )); then echo "2000"
    elif (( 10#$AHORA >= 1530 )); then echo "1530"
    elif (( 10#$AHORA >= 800 ));  then echo "0800"
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

# ──────────────────────────────────────────────────────────────────────────────
# OLLAMA, ANTES DE EMPEZAR: SI MURIÓ AL ARRANCAR, LEVANTARLO.
#
# EL 5-NOV-2026 ESTO COSTÓ UN DÍA ENTERO. `unattended-upgrades` instaló un
# kernel nuevo el día 4 a las 07:32; la máquina se apagó esa noche y arrancó el 5
# ya con el kernel nuevo. Docker lanzó `lab-ollama` diez segundos después de que
# el módulo NVIDIA empezara a cargarse, y no llegó: `nvml error: driver not
# loaded`. Con `unless-stopped`, un fallo al CREAR el contenedor no se reintenta
# —`RestartCount` quedó en 0— así que pasó el día caído y las tres franjas
# abortaron en `enrich`. Y como la copia del día solo se hace tras una franja de
# las 20:00 exitosa, ese día **tampoco hubo copia**.
#
# POR QUÉ LO ARREGLA EL SCHEDULER Y NO `enrich`. `enrich` corre DENTRO del
# contenedor `app`, que no tiene el socket de Docker: puede esperar a que Ollama
# aparezca —y ahora lo hace— pero no puede levantarlo. Esto corre en el host.
#
# ES PREDECIBLE, NO ALEATORIO. El equipo se apaga a las 21:00 y se enciende a las
# 6:00; `apt-daily-upgrade` dispara a los tres minutos de arrancar. El día que
# instala un kernel no rompe nada porque sigue corriendo el viejo: rompe a la
# mañana SIGUIENTE. Un kernel nuevo cada pocas semanas es una mañana perdida al
# mes, siempre la de después.
#
# Y NO PISA UNA PARADA DELIBERADA. `unless-stopped` significa que si alguien
# detiene el contenedor a mano, Docker respeta esa decisión; arrancarlo aquí sin
# mirar la pisaría.
#
# La firma de esa decisión es el CÓDIGO 0: `docker stop` manda SIGTERM y Ollama
# sale limpio. Cualquier otro código es un fallo —128 el de esta mañana, 137 un
# SIGKILL, 1 un arranque roto— y de un fallo sí toca recuperarse. La primera
# versión de esta condición miraba sólo el 128 y habría dejado morir un 137, que
# tampoco es una decisión de nadie.
OLLAMA_URL="${OLLAMA_BASE_URL:-http://127.0.0.1:11434}"
if ! curl -sf -m 5 "${OLLAMA_URL%/}/api/tags" >/dev/null 2>&1; then
    ESTADO=$(docker inspect lab-ollama \
        --format '{{.State.Status}}|{{.State.ExitCode}}|{{.State.Error}}' 2>/dev/null || echo "ausente||")
    SALIO="${ESTADO#*|}"; SALIO="${SALIO%%|*}"
    MOTIVO="${ESTADO##*|}"
    if [[ "$ESTADO" == ausente* ]]; then
        anotar "[scheduler] lab-ollama no existe: no es cosa de este servicio, se sigue"
    elif [[ "$SALIO" == "0" && -z "${MOTIVO// }" ]]; then
        anotar "[scheduler] lab-ollama detenido a propósito (código 0) — NO se toca"
    else
        anotar "[scheduler] lab-ollama caído por fallo (código $SALIO${MOTIVO:+, $MOTIVO}) — levantándolo"
        if docker start lab-ollama >> "$LOG" 2>&1; then
            for _ in $(seq 1 30); do
                curl -sf -m 3 "${OLLAMA_URL%/}/api/tags" >/dev/null 2>&1 && break
                sleep 2
            done
            if curl -sf -m 3 "${OLLAMA_URL%/}/api/tags" >/dev/null 2>&1; then
                anotar "[scheduler] lab-ollama responde: se continúa con la franja"
            else
                anotar "[scheduler] lab-ollama arrancó pero no responde tras 60s"
            fi
        else
            anotar "[scheduler] no se pudo levantar lab-ollama; la franja fallará en enrich"
        fi
    fi
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
        # El parte de la jornada, una vez al día y con cifras en lugar de
        # adjetivos: es lo que permite comparar con el de ayer y ver un cero
        # donde no debería haberlo. Los avisos 🔴 siguen siendo otra cosa —
        # interrumpen porque algo falló y hay que actuar.
        docker compose exec -T app python -m src.pipeline.resumen --enviar             >> "$LOG" 2>&1 && anotar "[scheduler] parte del día enviado"
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
