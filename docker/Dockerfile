# Imagen de la app del pipeline medallón MOD1-PRACTICA.
#
# Patrón UID/GID heredado de lab-nlp-fomc: el usuario del contenedor se crea con
# el UID/GID del host para que los archivos escritos en el bind mount ./data no
# queden como root en mi-pc.

FROM python:3.12-slim

ARG UID=1000
ARG GID=1000

# Dependencias del sistema:
#   build-essential  → compila ruedas que no traen binario para linux/arm64
#   libpq5           → runtime de PostgreSQL para psycopg (binary trae la suya,
#                      pero libpq5 evita sorpresas si se cambia a psycopg[c])
#   curl             → healthchecks y diagnóstico manual dentro del contenedor
#   git              → algunos paquetes de HuggingFace lo invocan al resolver
# Se limpia el índice de apt en la misma capa para no inflar la imagen.
RUN apt-get update && apt-get install --no-install-recommends -y \
        build-essential \
        libpq5 \
        curl \
        git \
    && rm -rf /var/lib/apt/lists/*

# Usuario no-root espejo del host. `|| true` porque en algunos hosts el GID/UID
# 1000 ya existe en la imagen base con otro nombre, y eso no es un error.
RUN groupadd -g "${GID}" app || true \
    && useradd -m -u "${UID}" -g "${GID}" -s /bin/bash app || true

WORKDIR /app

# requirements.txt se copia solo (antes que el código) para que la capa de pip
# se cachee y un cambio en src/ no reinstale torch de cero.
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /app/requirements.txt

# El código se monta como volumen en compose.yaml durante el desarrollo; se
# copia también aquí para que la imagen sea autosuficiente y reproducible.
COPY src/ /app/src/

# data/ es un bind mount en runtime; se crea aquí con el ownership correcto para
# que el primer arranque no falle si el host aún no tiene los subdirectorios.
RUN mkdir -p /app/data /app/data/hf_cache \
    && chown -R "${UID}:${GID}" /app

USER ${UID}:${GID}

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Sin ENTRYPOINT: el pipeline es batch y se invoca por etapas con
# `docker compose exec app python -m src.pipeline.<etapa>`.
CMD ["sleep", "infinity"]
