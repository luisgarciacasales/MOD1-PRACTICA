#!/usr/bin/env bash
#
# Segundo nivel de respaldo: trae las copias del servidor al Mac.
#
#   make replicar          replica y verifica
#   make replicar --ver    solo informa de lo que hay a cada lado
#
# POR QUÉ EXISTE. `scripts/backup.sh` guarda las copias en el MISMO disco que
# los datos: un solo NVMe, sin RAID, sin ZFS (verificado el 3-sep-2026). Eso
# protege del error humano —un `down -v`, una migración mala, un DELETE
# equivocado—, que es el riesgo frecuente, pero no del fallo del disco, porque
# ese modo de fallo se lleva original y copia a la vez.
#
# Y en este proyecto hay dos cosas que no se pueden volver a obtener de ninguna
# forma: los 29 reportes de Banorte de 2018-2024 (su página de RI solo publica
# los cinco trimestres más recientes) y las inferencias del modelo local, que no
# son reproducibles porque el modelo no es determinista.
#
# CORRE EN EL MAC, no en el servidor: el canal SSH ya existe en esa dirección,
# así que no hay que darle credenciales nuevas al servidor. Solo lee de allá.
#
# NO ES UN ESPEJO, y es deliberado. Sin `--delete`: si alguien borrara el árbol
# de copias del servidor, un espejo replicaría el borrado y el segundo nivel no
# habría servido de nada. Aquí el Mac acumula y aplica su propia retención, más
# larga que la del servidor. Un archivo independiente, no un reflejo.

set -euo pipefail

REMOTO="${REMOTE:-mi-pc}"
ORIGEN="${BACKUP_DIR_REMOTO:-augmented/backups/MOD1-PRACTICA}"
DESTINO="${REPLICA_DIR:-$HOME/augmented/backups/MOD1-PRACTICA}"
RETENCION="${REPLICA_RETENCION:-30}"

# Ejecutarlo en el servidor copiaría el disco sobre sí mismo, que es
# exactamente lo que este script existe para evitar.
if [[ "$(hostname -s)" == "jose-gaming" ]]; then
    echo "[replicar] esto se ejecuta en el Mac, no en el servidor" >&2
    exit 1
fi

resumen() {
    echo "[replicar] en el servidor: $(ssh "$REMOTO" "du -sh $ORIGEN 2>/dev/null | cut -f1") · \
$(ssh "$REMOTO" "find $ORIGEN -maxdepth 1 -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' '") copias"
    if [[ -d "$DESTINO" ]]; then
        echo "[replicar] en el Mac:       $(du -sh "$DESTINO" | cut -f1) · \
$(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ') copias"
    else
        echo "[replicar] en el Mac:       (todavía nada)"
    fi
}

if [[ "${1:-}" == "--ver" ]]; then resumen; exit 0; fi

mkdir -p "$DESTINO"
echo "[replicar] trayendo de ${REMOTO}…"
# -H es imprescindible: sin él, cada copia se traería entera y las cinco
# actuales ocuparían 1,7 GB en vez de 504 MB. `openrsync` de macOS lo soporta
# aunque no lo anuncie en su --help; comprobado por inodos antes de confiar.
# Sin `--info=stats2`: `openrsync` no lo reconoce. Y sin tubería a `grep`: la
# primera versión canalizaba la salida y remataba con `|| true`, de modo que un
# fallo del propio rsync quedaba enmascarado y el script seguía como si nada
# hasta reventar más tarde con un mensaje que no señalaba la causa.
rsync -aH "$REMOTO:$ORIGEN/" "$DESTINO/"

# --- Verificación: que lo copiado sea idéntico, no solo que exista ----------
# Un respaldo que nadie comprueba es una suposición. Se contrasta el volcado
# más reciente byte a byte mediante su hash, a los dos lados.
ULTIMO="$(ssh "$REMOTO" "readlink -f $ORIGEN/ultimo" | xargs basename)"
REMOTO_SHA="$(ssh "$REMOTO" "sha256sum $ORIGEN/$ULTIMO/postgres.dump" | cut -d' ' -f1)"
LOCAL_SHA="$(shasum -a 256 "$DESTINO/$ULTIMO/postgres.dump" | cut -d' ' -f1)"

if [[ "$REMOTO_SHA" != "$LOCAL_SHA" ]]; then
    echo "[replicar] ERROR: el volcado replicado NO coincide con el del servidor" >&2
    echo "           servidor: $REMOTO_SHA" >&2
    echo "           Mac:      $LOCAL_SHA" >&2
    exit 1
fi
echo "[replicar] verificado: $ULTIMO/postgres.dump idéntico (${REMOTO_SHA:0:16}…)"

# --- Retención propia, más larga que la del servidor ------------------------
# `head -n -N` es de GNU: el `head` de macOS no acepta contadores negativos, y
# este script corre precisamente en el Mac. Se calcula el excedente a mano.
total=$(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
sobran=""
if (( total > RETENCION )); then
    sobran=$(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | sort | head -n "$(( total - RETENCION ))")
fi
if [[ -n "$sobran" ]]; then
    echo "$sobran" | while read -r viejo; do
        echo "[replicar] retirando $(basename "$viejo") (retención local: $RETENCION)"
        rm -rf "$viejo"
    done
fi

resumen
echo "[replicar] los reportes de Banorte y las inferencias del modelo ya viven en dos discos."
