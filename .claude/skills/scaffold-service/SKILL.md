---
name: scaffold-service
description: Crear o extender la estructura del servicio medallón MOD1-PRACTICA compatible con las convenciones del servidor mi-pc — compose.yaml que reusa lab-ollama por host-gateway, puertos solo loopback, patrón .env/UID:GID, layout de carpetas Bronze/Silver/Gold y helpers Mac (README_MAC.md, Makefile). Úsalo cuando toque materializar el esqueleto del proyecto.
---

# scaffold-service — Estructura del servicio medallón

Genera el esqueleto del proyecto siguiendo las convenciones ya establecidas en `~/augmented/services/` (observadas en `lab-ollama`, `lab-nlp-fomc`). **No** implementa lógica de pipeline (eso lo guía `medallion-pipeline`); solo el andamiaje.

> Estado actual: el arnés está creado, el scaffold **aún no**. Este skill es la guía para materializarlo cuando el usuario lo pida.

## Convenciones del servidor (respétalas)

- **Un servicio por carpeta** en `~/augmented/services/<NAME>/`.
- **Puertos solo en `127.0.0.1`** + acceso por túnel SSH. Documenta en el compose *por qué* cada puerto existe (estilo `lab-nlp-fomc`).
- **`compose.yaml`** como nombre de archivo (moderno, igual que `lab-ollama`), con `name:` de proyecto explícito (p. ej. `name: mod1-practica`).
- **`container_name`, `image` con tag de versión, `user: "${UID:-1000}:${GID:-1000}"`.**
- **Comentarios con rationale** en el compose: la siguiente persona (o agente) debe entender el porqué de cada decisión.

## Layout objetivo

```
MOD1-PRACTICA/
├── compose.yaml                 # servicios: app (pipeline), postgres. Reusa lab-ollama externo.
├── .env.example                 # plantilla de secretos (BANXICO_TOKEN, POSTGRES_*, etc.)
├── .gitignore                   # excluye data/, *.index, .env, __pycache__, volúmenes
├── Makefile                     # atajos Mac: make deploy / logs / verify / tunnel
├── README_MAC.md                # cómo operar desde el Mac (túneles, deploy)
├── docker/Dockerfile            # imagen de la app (Python + deps)
├── requirements.txt             # yfinance, pydantic, pandas_market_calendars, faiss-cpu/gpu,
│                                # requests-cache, aiohttp, sentence-transformers, psycopg, ...
├── src/
│   ├── contracts/               # modelos Pydantic (ver data-contracts)
│   ├── pipeline/                # ingest, validate, enrich, transform, correlate, index
│   ├── sources/                 # bmv, rss, yfinance, banxico, finnovista
│   └── config/                  # mapeo sector→proxy, series BANXICO, tickers prioritarios
└── data/                        # BRONZE/SILVER/GOLD — gitignored, vive solo en mi-pc
    ├── bronze/{news,market}/
    ├── cache/requests_cache.sqlite
    └── faiss/index.index
```

## Conexión a Ollama (host-gateway — decisión del arnés)

En el `compose.yaml`, el servicio `app` **no** define Ollama; usa el `lab-ollama` compartido:
```yaml
services:
  app:
    extra_hosts:
      - "host.docker.internal:host-gateway"
    environment:
      OLLAMA_BASE_URL: "http://host.docker.internal:11434"
```
> **Ruta de graduación (no ahora):** si crecen los servicios que se hablan entre sí, migrar a una red externa compartida `augmented-net`: crear `docker network create augmented-net`, engancharla a `lab-ollama` (editar su compose) y a `app` como `networks: [augmented-net]`, y llamar a `http://lab-ollama:11434`. Esto **toca un servicio compartido**: hazlo solo con autorización explícita.

## PostgreSQL

- Contenedor `postgres:15` con volumen nombrado persistente; puerto solo loopback (`127.0.0.1:5432:5432`).
- Credenciales vía `.env` (nunca en git). Extensión `pgvector` si se decide almacenar embeddings en PG además de FAISS.

## `.gitignore` mínimo (frontera de git)

```
.env
data/
*.index
*.sqlite
__pycache__/
*.pyc
.venv/
```

## Reglas

- **Nunca** generes un `.env` con secretos reales; solo `.env.example` con placeholders.
- **Nunca** levantes Ollama propio (invariante del arnés).
- Reusa el estilo documentado de los servicios vecinos; consulta `lab-nlp-fomc/docker-compose.yml` como referencia de tono y rationale.
- Tras scaffold: `docker compose config --quiet` debe pasar antes del primer deploy.
