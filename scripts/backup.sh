#!/usr/bin/env bash
#
# Copia de seguridad de lo que NO se puede volver a obtener.
#
#   bash scripts/backup.sh              crea un snapshot nuevo
#   bash scripts/backup.sh --listar     muestra los que hay
#
# Se ejecuta en el HOST (mi-pc), no dentro de un contenedor: necesita hablar
# con docker y escribir fuera del árbol del repositorio.
#
# QUÉ SE COPIA Y POR QUÉ
#
#   postgres.dump     Silver y Gold. Incluye `gold_enriched_news`, que es la
#                     ÚNICA copia de la inferencia del LLM local: no es
#                     reproducible (el modelo no es determinista — al recalibrar
#                     un prompt cambiaron las 1.350 noticias de veredicto) y
#                     cuesta ~40 min de GPU. También `gold_brief_ejecuciones`,
#                     que costó dinero real.
#   bronze/           La capa inmutable de la que se regenera Silver entero.
#   manual_dropzone/  Los PDF ORIGINALES. Bronze guarda las cifras extraídas,
#                     no el documento, y los 29 reportes de Banorte de
#                     2018-2024 NO se pueden volver a descargar: su página de
#                     RI solo publica los cinco trimestres más recientes.
#   briefs/, logs/    Pequeños y con valor de auditoría.
#
# QUÉ NO, deliberadamente
#
#   faiss/            Se reconstruye desde Silver y `verify` lo comprueba.
#   hf_cache/         Modelos que se vuelven a descargar.
#   cache/            Caché HTTP.
#   .env y ~/augmented/secrets/
#                     Son SECRETOS y no van a un archivo de copia. Ten esos
#                     valores en un gestor de contraseñas: si se pierde este
#                     disco, el backup restaura los datos pero el token de
#                     Banxico y la clave de Anthropic los repones tú.
#
# Los archivos usan `rsync --link-dest`: cada snapshot se ve completo pero
# comparte por enlace duro todo lo que no cambió desde el anterior. Bronze es
# inmutable y acumulativo, así que un snapshot diario cuesta solo lo nuevo.

set -euo pipefail

PROYECTO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESTINO="${BACKUP_DIR:-$HOME/augmented/backups/MOD1-PRACTICA}"
RETENCION="${BACKUP_RETENCION:-14}"

if [[ "${1:-}" == "--listar" ]]; then
    if [[ ! -d "$DESTINO" ]]; then
        echo "[backup] todavía no hay copias en $DESTINO"; exit 0
    fi
    echo "[backup] copias en $DESTINO"
    du -sh "$DESTINO"/*/ 2>/dev/null | sort -k2 || echo "  (ninguna)"
    echo "[backup] espacio total ocupado (los enlaces duros no se cuentan dos veces):"
    du -sh "$DESTINO"
    exit 0
fi

cd "$PROYECTO"
SELLO="$(date +%Y-%m-%d_%H%M)"
SNAPSHOT="$DESTINO/$SELLO"
ANTERIOR="$DESTINO/ultimo"

mkdir -p "$SNAPSHOT/archivos"

# --- Base de datos ---------------------------------------------------------
# Formato `custom` (-Fc): comprimido y restaurable de forma selectiva, tabla a
# tabla, que es lo que hace falta cuando lo que se quiere recuperar es una sola
# tabla y no la base entera.
echo "[backup] volcando PostgreSQL…"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-mod1}" \
    -d "${POSTGRES_DB:-mod1_practica}" -Fc > "$SNAPSHOT/postgres.dump"

# Un volcado que no se puede leer no es una copia. Se comprueba SIEMPRE, aquí
# y ahora, no el día que haga falta restaurarlo.
# `pg_restore` vive en el contenedor, no en el host, así que la comprobación
# entra por stdin igual que salió el volcado.
if ! docker compose exec -T postgres pg_restore --list \
        < "$SNAPSHOT/postgres.dump" > "$SNAPSHOT/postgres.indice.txt" 2>/dev/null; then
    echo "[backup] ERROR: el volcado no es legible por pg_restore" >&2
    rm -rf "$SNAPSHOT"
    exit 1
fi
TABLAS=$(grep -c "TABLE DATA" "$SNAPSHOT/postgres.indice.txt" || true)

# --- Archivos --------------------------------------------------------------
ENLACE=()
[[ -d "$ANTERIOR/archivos" ]] && ENLACE=(--link-dest="$ANTERIOR/archivos")

echo "[backup] copiando archivos…"
rsync -a --delete "${ENLACE[@]}" \
    --exclude="cache/" --exclude="hf_cache/" --exclude="faiss/" \
    data/ "$SNAPSHOT/archivos/"

# --- Manifiesto ------------------------------------------------------------
{
    echo "proyecto:    MOD1-PRACTICA"
    echo "creado:      $(date -Iseconds)"
    echo "host:        $(hostname)"
    echo "commit:      $(git rev-parse --short HEAD 2>/dev/null || echo '?')"
    echo "tablas:      $TABLAS con datos"
    echo "postgres:    $(du -h "$SNAPSHOT/postgres.dump" | cut -f1)"
    echo "bronze:      $(du -sh "$SNAPSHOT/archivos/bronze" 2>/dev/null | cut -f1)"
    echo "dropzone:    $(find "$SNAPSHOT/archivos/manual_dropzone" -name '*.pdf' 2>/dev/null | wc -l) PDF"
    echo
    echo "NO INCLUIDO: .env y ~/augmented/secrets/ (son secretos, repónlos a mano)"
    echo "             faiss/ (se reconstruye), hf_cache/ y cache/ (se redescargan)"
    echo
    echo "RESTAURAR la base entera:"
    echo "  docker compose exec -T postgres pg_restore -U mod1 -d mod1_practica \\"
    echo "      --clean --if-exists < $SNAPSHOT/postgres.dump"
    echo "RESTAURAR una sola tabla:"
    echo "  ... pg_restore -U mod1 -d mod1_practica --data-only -t gold_enriched_news < ..."
    echo "RESTAURAR archivos:"
    echo "  rsync -a $SNAPSHOT/archivos/ $PROYECTO/data/"
} > "$SNAPSHOT/MANIFIESTO.txt"

ln -sfn "$SNAPSHOT" "$ANTERIOR"

# --- Retención -------------------------------------------------------------
# `ultimo` es un symlink, no un snapshot: se excluye del recuento para no
# borrar por accidente aquel al que apunta.
sobran=$(find "$DESTINO" -maxdepth 1 -mindepth 1 -type d | sort | head -n -"$RETENCION")
if [[ -n "$sobran" ]]; then
    echo "$sobran" | while read -r viejo; do
        echo "[backup] retirando $(basename "$viejo") (retención: $RETENCION)"
        rm -rf "$viejo"
    done
fi

echo "[backup] listo: $SNAPSHOT"
cat "$SNAPSHOT/MANIFIESTO.txt" | head -9
echo "[backup] ocupación total del árbol de copias: $(du -sh "$DESTINO" | cut -f1)"
