#!/usr/bin/env bash
#
# Instala, retira o inspecciona el LaunchAgent que replica al Mac.
#
#   make replicar-instalar      instala y comprueba que de verdad corre
#   make replicar-estado        qué hay, cuándo corrió y si va atrasado
#   make replicar-quitar        retira el agente (no borra las copias)
#
# CORRE EN EL MAC. El plist se genera desde la plantilla versionada porque la
# ruta del repositorio no puede vivir en un fichero de git, y se instala en
# ~/Library/LaunchAgents/, que está FUERA del repositorio — la frontera de git
# de este proyecto (invariante 1) deja fuera cualquier cosa que sea estado de la
# máquina, y un plist instalado lo es.

set -euo pipefail

AQUI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$AQUI/.." && pwd)"
ETIQUETA="com.josegc.mod1.replicar"
PLANTILLA="$AQUI/launchd/${ETIQUETA}.plist.tpl"
INSTALADO="$HOME/Library/LaunchAgents/${ETIQUETA}.plist"
REGISTRO="${REPLICA_LOG:-$HOME/augmented/logs/replicar.log}"
DESTINO="${REPLICA_DIR:-$HOME/augmented/backups/MOD1-PRACTICA}"
DOMINIO="gui/$(id -u)"

if [[ "$(hostname -s)" == "jose-gaming" ]]; then
    echo "[replicar] esto se instala en el Mac, no en el servidor" >&2
    exit 1
fi

estado() {
    echo "agente:     $ETIQUETA"
    if launchctl print "$DOMINIO/$ETIQUETA" >/dev/null 2>&1; then
        echo "instalado:  sí ($INSTALADO)"
        launchctl print "$DOMINIO/$ETIQUETA" 2>/dev/null \
            | grep -E "state|last exit code|runs" | sed 's/^/            /'
    else
        echo "instalado:  NO"
    fi

    if [[ -d "$DESTINO" ]]; then
        local ultima
        ultima="$(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | sort | tail -1)"
        echo "copias:     $(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ') · $(du -sh "$DESTINO" | cut -f1)"
        echo "más nueva:  $(basename "$ultima") · hace $(( ( $(date +%s) - $(stat -f %m "$ultima") ) / 86400 ))d"
    else
        echo "copias:     ninguna todavía"
    fi

    if [[ -f "$REGISTRO" ]]; then
        echo "registro:   $REGISTRO"
        tail -5 "$REGISTRO" | sed 's/^/            /'
    else
        echo "registro:   (vacío, aún no ha corrido)"
    fi
}

quitar() {
    # `bootout` es la forma moderna; `unload` queda como respaldo para macOS
    # antiguos. Ninguna de las dos borra las copias — solo desprograma.
    launchctl bootout "$DOMINIO/$ETIQUETA" 2>/dev/null \
        || launchctl unload "$INSTALADO" 2>/dev/null || true
    rm -f "$INSTALADO"
    echo "[replicar] agente retirado. Las copias de $DESTINO NO se han tocado."
}

case "${1:-instalar}" in
    estado) estado; exit 0 ;;
    quitar) quitar; exit 0 ;;
    instalar) ;;
    *) echo "uso: $0 [instalar|estado|quitar]" >&2; exit 2 ;;
esac

# --- Instalación -----------------------------------------------------------
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/augmented/logs"

sed -e "s|__REPO__|$REPO|g" -e "s|__HOME__|$HOME|g" "$PLANTILLA" >"$INSTALADO"

# Un plist mal formado lo rechaza launchd con un mensaje poco claro; validarlo
# aquí señala el problema en el fichero y no en el cargador.
plutil -lint "$INSTALADO" >/dev/null

# Recargar, no solo cargar: instalar encima de una versión previa sin retirarla
# deja la vieja corriendo y da la falsa impresión de haber actualizado nada.
launchctl bootout "$DOMINIO/$ETIQUETA" 2>/dev/null || true
launchctl bootstrap "$DOMINIO" "$INSTALADO"

echo "[replicar] agente instalado: $INSTALADO"

# --- Verificación: que corra DE VERDAD, no que esté registrado -------------
# Esta parte es el motivo de que exista el script. El riesgo real de un
# LaunchAgent que usa SSH no es el plist, es el acceso a la clave: la de este
# Mac tiene passphrase y se resuelve contra el agente del usuario. Si launchd no
# le pasa SSH_AUTH_SOCK, el agente queda instalado, silencioso y sin replicar
# nada — y se descubriría el día que hiciera falta la copia. Así que se dispara
# una ejecución y se comprueba el resultado.
# `wc -l <fichero` no sirve aquí: en la primera instalación el registro todavía
# no existe, y el fallo de la redirección lo emite bash — no lo tapa el
# `2>/dev/null` del propio wc, así que la espera imprimía un error por vuelta.
lineas_del_registro() {
    if [[ -f "$REGISTRO" ]]; then wc -l <"$REGISTRO" | tr -d ' '; else echo 0; fi
}

antes=$(lineas_del_registro)
ahora=$antes
echo "[replicar] disparando una ejecución de prueba…"
launchctl kickstart -k "$DOMINIO/$ETIQUETA"

for _ in $(seq 1 60); do
    ahora=$(lineas_del_registro)
    (( ahora > antes )) && break
    sleep 2
done

if (( ahora <= antes )); then
    echo "[replicar] ATENCIÓN: el agente no escribió en el registro en 120 s." >&2
    echo "           Revisa $HOME/augmented/logs/replicar.launchd.log" >&2
    exit 1
fi

ultima_linea="$(tail -1 "$REGISTRO")"
echo "[replicar] el agente escribió: $ultima_linea"

case "$ultima_linea" in
    *OK*)            echo "[replicar] verificado: replica y accede a la clave SSH desde launchd." ;;
    *SIN-SERVIDOR*)  echo "[replicar] el agente corre, pero el servidor no respondía en la prueba."
                     echo "           Eso NO confirma el acceso a la clave SSH: repite con el servidor encendido." ;;
    *)               echo "[replicar] ATENCIÓN: ejecución con resultado inesperado, míralo." >&2; exit 1 ;;
esac
