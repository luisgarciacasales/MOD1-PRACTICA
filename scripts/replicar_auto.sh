#!/usr/bin/env bash
#
# Envoltorio desatendido de `replicar.sh`, para que lo lance launchd.
#
#   make replicar-instalar     instala el LaunchAgent
#   make replicar-estado       qué hay, cuándo corrió y si va atrasado
#
# POR QUÉ EXISTE Y NO SE PROGRAMA `replicar.sh` DIRECTAMENTE. Ese script está
# escrito para una persona mirando la terminal: si el servidor no contesta,
# falla con un error, que es lo correcto cuando lo acabas de invocar a mano.
# Desatendido ese mismo comportamiento es ruido, porque **no contestar es lo
# normal**: el servidor es un equipo de escritorio que se apaga entre las 18:00
# y las 23:00, y el Mac es un portátil que puede estar dormido o fuera del
# tailnet. Programar el script crudo produciría fallos diarios esperados, y una
# alerta que salta todos los días deja de leerse — el mismo argumento que
# sostiene el silencio de `avisos.py` en el servidor.
#
# LA DISTINCIÓN QUE HACE ESTE ENVOLTORIO: separa "hoy no se pudo" de "llevamos
# demasiado sin poder". Lo primero se anota y se calla. Lo segundo avisa, porque
# es el estado que de verdad importa — que la segunda copia está vieja— y da
# igual si la causa fue el servidor, la red o el portátil.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${REPLICA_DIR:-$HOME/augmented/backups/MOD1-PRACTICA}"
REMOTO="${REMOTE:-mi-pc}"
REGISTRO="${REPLICA_LOG:-$HOME/augmented/logs/replicar.log}"
CANDADO="${REPLICA_LOCK:-$HOME/augmented/logs/replicar.lock}"

# Días que puede tener la copia más reciente antes de que sea un problema. Tres
# tolera un fin de semana con el servidor apagado sin avisar de nada.
UMBRAL_DIAS="${REPLICA_UMBRAL_DIAS:-3}"

mkdir -p "$(dirname "$REGISTRO")"

anotar() { echo "$(date +%Y-%m-%dT%H:%M:%S%z) $*" >>"$REGISTRO"; }

# Aviso nativo de macOS. NO se reusa el bot de Telegram del servidor a
# propósito: su token vive en `~/augmented/secrets/` del servidor, y traerlo al
# Mac multiplicaría las copias de un secreto para ganar un canal que aquí no
# hace falta. Si el Mac está encendido, su propio centro de notificaciones basta.
avisar() {
    /usr/bin/osascript -e "display notification \"$1\" with title \"Réplica MOD1-PRACTICA\"" \
        >/dev/null 2>&1 || true
}

# Edad en días de la copia más reciente, o -1 si no hay ninguna.
edad_de_la_replica() {
    local ultima
    ultima="$(/usr/bin/find "$DESTINO" -maxdepth 1 -mindepth 1 -type d 2>/dev/null | sort | tail -1)"
    if [[ -z "$ultima" ]]; then echo -1; return; fi
    # `stat -f %m` es de BSD, que es lo que hay en macOS; este script no corre
    # en Linux (el propio replicar.sh lo impide).
    local modificada ahora
    modificada="$(/usr/bin/stat -f %m "$ultima")"
    ahora="$(date +%s)"
    echo $(( (ahora - modificada) / 86400 ))
}

# --- Un solo replicador a la vez -------------------------------------------
# launchd puede encadenar una ejecución programada con la de `RunAtLoad`, y dos
# rsync sobre el mismo árbol con enlaces duros es una forma de corromperlo.
# `mkdir` es atómico; un fichero de PID no lo es.
if ! mkdir "$CANDADO" 2>/dev/null; then
    anotar "OMITIDA  ya hay una réplica en curso ($CANDADO)"
    exit 0
fi
trap 'rmdir "$CANDADO" 2>/dev/null || true' EXIT

# --- ¿Está el servidor? ----------------------------------------------------
# Se pregunta antes de intentar la réplica para poder distinguir en el registro
# "no había servidor" de "el servidor estaba y la réplica falló", que piden
# reacciones distintas.
if ! ssh -o BatchMode=yes -o ConnectTimeout=10 "$REMOTO" true 2>/dev/null; then
    edad="$(edad_de_la_replica)"
    anotar "SIN-SERVIDOR  $REMOTO no responde · copia más reciente: ${edad}d"
    if (( edad < 0 || edad > UMBRAL_DIAS )); then
        avisar "Sin replicar desde hace ${edad}d y el servidor no responde."
        anotar "AVISO  copia de ${edad}d por encima del umbral de ${UMBRAL_DIAS}d"
    fi
    exit 0
fi

# --- Réplica ---------------------------------------------------------------
salida="$(mktemp)"
trap 'rm -f "$salida"; rmdir "$CANDADO" 2>/dev/null || true' EXIT

if bash "$AQUI/replicar.sh" >"$salida" 2>&1; then
    anotar "OK  $(grep -c . "$salida") líneas · $(grep 'verificado:' "$salida" | tail -1)"
    exit 0
fi

# El servidor SÍ estaba, así que esto es un fallo de verdad: el rsync, o —peor—
# la verificación del hash, que significaría una copia que no sirve.
anotar "FALLO  el servidor respondía pero la réplica no terminó"
sed 's/^/         /' "$salida" >>"$REGISTRO"
avisar "La réplica falló con el servidor accesible. Mira el registro."
exit 1
