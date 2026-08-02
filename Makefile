# Makefile — atajos para operar MOD1-PRACTICA.
#
# Funciona en LOS DOS LADOS y se adapta solo:
#   · en el Mac        → cada comando viaja por `ssh mi-pc`
#   · en mi-pc         → los mismos comandos se ejecutan en local, sin SSH
#     (el alias `mi-pc` solo existe en el ~/.ssh/config del Mac; desde el propio
#      servidor no resuelve, y ese fue el error que motivó esta detección)
#
# Los targets que solo tienen sentido en el Mac (push, tunnel) fallan con un
# mensaje explícito si se invocan en el servidor, en vez de con un error de DNS.
#
# Los procedimientos canónicos viven en los skills; esto son solo envoltorios:
#   deploy      → .claude/skills/deploy/SKILL.md
#   ops/logs/ps → .claude/skills/remote-ops/SKILL.md
#   verify      → .claude/skills/acceptance-verify/SKILL.md

REMOTE          ?= mi-pc
REMOTE_DIR      ?= ~/augmented/services/MOD1-PRACTICA
SERVER_HOSTNAME ?= jose-gaming
PG_PORT         ?= 5433

HOST := $(shell hostname -s 2>/dev/null)

ifeq ($(HOST),$(SERVER_HOSTNAME))
  # Ejecutando EN el servidor: sin SSH. `sh -c` recibe la misma cadena entre
  # comillas que recibiría ssh, así que los targets no cambian de forma.
  EN_SERVIDOR := 1
  RUN         := sh -c
  RUN_T       := sh -c
  DIR         := .
  CONTEXTO    := mi-pc (local, sin SSH)
else
  EN_SERVIDOR :=
  RUN         := ssh $(REMOTE)
  RUN_T       := ssh -t $(REMOTE)
  DIR         := $(REMOTE_DIR)
  CONTEXTO    := Mac → $(REMOTE) por SSH
endif

COMPOSE := cd $(DIR) && docker compose

# Guardia para targets exclusivos del Mac.
# OJO: $(call) separa argumentos por comas, así que el mensaje que se le pase
# NO debe contener ninguna o quedará truncado en la primera.
define solo_mac
	@if [ -n "$(EN_SERVIDOR)" ]; then \
		echo "ERROR: '$@' solo se ejecuta desde el Mac."; \
		echo "  Motivo: $(1)"; \
		exit 1; \
	fi
endef

.DEFAULT_GOAL := help
.PHONY: help where init deploy push pull up down build ps logs shell psql config tunnel gpu ollama ingest validate enrich transform correlate index search demo evidencia bronze migrate test verify

help: ## Muestra esta ayuda
	@echo "Contexto detectado: $(CONTEXTO)"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

where: ## Muestra dónde se ejecutarán los comandos y por qué
	@echo "hostname       : $(HOST)"
	@echo "contexto       : $(CONTEXTO)"
	@echo "directorio     : $(DIR)"
	@echo "prefijo de run : $(RUN)"

## --- Deploy (Mac → GitHub → mi-pc) -----------------------------------------

push: ## Commitea y publica a GitHub (uso: make push M="mensaje") — SOLO Mac
	$(call solo_mac,invariante 2 — mi-pc solo hace git pull y nunca commitea)
	@test -n "$(M)" || (echo "ERROR: usa make push M=\"mensaje de commit\""; exit 1)
	@git status --short
	@git add -A && git commit -m "$(M)" && git push

pull: ## git pull determinista (descarta drift local; nunca commitea — invariante 2)
	$(RUN) 'cd $(DIR) && git fetch --all --prune && git reset --hard @{u} && git log -1 --oneline'

deploy: pull up ps ## pull + up + ps (el .env del servidor NO se toca)

## --- Ciclo de vida ----------------------------------------------------------

init: ## Crea el árbol data/ con el ownership correcto (ANTES del primer up)
	@echo "Creando data/ en $(CONTEXTO) — si no existe, Docker lo crearía como root."
	$(RUN) 'cd $(DIR) && mkdir -p data/bronze/news data/bronze/market \
		data/silver data/gold data/cache data/faiss data/hf_cache && ls -la data'
	@echo "Recuerda: el .env se crea A MANO en el servidor desde .env.example (invariante 3)."

build: ## Reconstruye la imagen de la app
	$(RUN) '$(COMPOSE) build'

up: ## Levanta los servicios
	$(RUN) '$(COMPOSE) up -d --build'

down: ## Detiene los servicios (SIN borrar volúmenes)
	$(RUN) '$(COMPOSE) down'

ps: ## Estado de los servicios
	$(RUN) '$(COMPOSE) ps'

config: ## Valida la sintaxis del compose
	$(RUN) '$(COMPOSE) config --quiet && echo "compose OK"'

logs: ## Últimas 100 líneas (uso: make logs S=app)
	$(RUN) '$(COMPOSE) logs -n 100 $(S)'

shell: ## Shell interactiva dentro del contenedor app
	$(RUN_T) 'cd $(DIR) && docker compose exec app bash'

psql: ## psql interactivo contra Silver/Gold
	$(RUN_T) 'cd $(DIR) && docker compose exec postgres psql -U $${POSTGRES_USER:-mod1} -d $${POSTGRES_DB:-mod1_practica}'

## --- Observabilidad ---------------------------------------------------------

gpu: ## VRAM y utilización de la RTX 5080 (presupuesto crítico: 16 GB)
	$(RUN) 'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv'

ollama: ## Modelos disponibles en el lab-ollama compartido
	$(RUN) 'curl -s http://127.0.0.1:11434/api/tags'

tunnel: ## Túnel SSH: Postgres remoto → 127.0.0.1:$(PG_PORT) en el Mac — SOLO Mac
	$(call solo_mac,en el servidor el puerto ya es local en 127.0.0.1:5432)
	@echo "Postgres de mi-pc disponible en 127.0.0.1:$(PG_PORT) — Ctrl-C para cerrar"
	ssh -N -L $(PG_PORT):127.0.0.1:5432 $(REMOTE)

## --- Pipeline ---------------------------------------------------------------

enrich: ## Enriquecimiento NLP con Ollama (uso: make enrich ARGS="--limit 10")
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.enrich $(ARGS)'

transform: ## Métricas derivadas de mercado y macro (Silver -> Gold)
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.transform'

correlate: ## JOIN temporal noticias<->precios con calendario XMEX
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.correlate $(ARGS)'

validate: ## Valida Bronze y carga Silver (uso: make validate ARGS="--date 2026-08-02")
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.validate $(ARGS)'

ingest: ## Ingesta las 5 fuentes hacia Bronze (uso: make ingest ARGS="--dry-run")
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.ingest $(ARGS)'

bronze: ## Inventario de lotes en Bronze
	$(RUN) 'cd $(DIR) && find data/bronze -name metadata.json -printf "%h\n" 2>/dev/null | sed "s|data/bronze/||" | sort || echo "(Bronze vacío)"'

index: ## Vectoriza y construye el índice FAISS
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.index $(ARGS)'

search: ## Búsqueda semántica (uso: make search Q="tasas de Banxico")
	@test -n "$(Q)" || (echo 'ERROR: usa make search Q="tu consulta"'; exit 1)
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.search "$(Q)"'

## --- Esquema y pruebas ------------------------------------------------------

migrate: ## Aplica las migraciones SQL de sql/ (idempotente)
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.migrate'

test: ## Ejecuta las pruebas de los contratos dentro del contenedor
	$(RUN) '$(COMPOSE) exec -T app python -m pytest tests/ -q'

## --- Aceptación -------------------------------------------------------------

demo: ## Demostración de punta a punta 2x + tabla de evidencia (entregable)
	$(RUN) '$(COMPOSE) exec -T app python scripts/demo_e2e.py $(ARGS)'

evidencia: ## Trae el informe de evidencia del servidor al Mac
	$(call solo_mac,el informe se copia DESDE el servidor)
	scp $(REMOTE):$(REMOTE_DIR)/data/evidencia_e2e.md ./evidencia_e2e.md
	@echo "Escrito en ./evidencia_e2e.md (gitignorado si lo prefieres fuera del repo)"

verify: ## Checks de la Definición de Terminado (PRD §8)
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.verify $(ARGS)'
