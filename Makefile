# Makefile — atajos para operar MOD1-PRACTICA DESDE EL MAC.
#
# Todo lo que toca el servidor va por SSH: el pipeline se ejecuta en mi-pc, no
# aquí (ver docs/HARNESS.md, ADR-1). Este Makefile no ejecuta docker en local.
#
# Los procedimientos canónicos viven en los skills; esto son solo envoltorios:
#   deploy      → .claude/skills/deploy/SKILL.md
#   ops/logs/ps → .claude/skills/remote-ops/SKILL.md
#   verify      → .claude/skills/acceptance-verify/SKILL.md

REMOTE      ?= mi-pc
REMOTE_DIR  ?= ~/augmented/services/MOD1-PRACTICA
SSH         := ssh $(REMOTE)
COMPOSE     := cd $(REMOTE_DIR) && docker compose
PG_PORT     ?= 5433

.DEFAULT_GOAL := help
.PHONY: help init deploy push pull up down build ps logs shell psql config tunnel gpu ollama verify

help: ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

## --- Deploy (Mac → GitHub → mi-pc) -----------------------------------------

push: ## Commitea y publica a GitHub (uso: make push M="mensaje")
	@test -n "$(M)" || (echo "ERROR: usa make push M=\"mensaje de commit\""; exit 1)
	@git status --short
	@git add -A && git commit -m "$(M)" && git push

pull: ## git pull determinista en mi-pc (nunca commitea allí — invariante 2)
	$(SSH) 'cd $(REMOTE_DIR) && git fetch --all --prune && git reset --hard @{u} && git log -1 --oneline'

deploy: pull up ps ## pull + up + ps en mi-pc (el .env del servidor NO se toca)

## --- Ciclo de vida en mi-pc -------------------------------------------------

init: ## Crea el árbol data/ en mi-pc con el ownership correcto (ANTES del primer up)
	@echo "Creando data/ en $(REMOTE):$(REMOTE_DIR) — si no existe, Docker lo crearía como root."
	$(SSH) 'cd $(REMOTE_DIR) && mkdir -p data/bronze/news data/bronze/market \
		data/silver data/gold data/cache data/faiss data/hf_cache && ls -la data'
	@echo "Recuerda: el .env se crea A MANO en el servidor desde .env.example (invariante 3)."

build: ## Reconstruye la imagen de la app en mi-pc
	$(SSH) '$(COMPOSE) build'

up: ## Levanta los servicios en mi-pc
	$(SSH) '$(COMPOSE) up -d --build'

down: ## Detiene los servicios (SIN borrar volúmenes)
	$(SSH) '$(COMPOSE) down'

ps: ## Estado de los servicios
	$(SSH) '$(COMPOSE) ps'

config: ## Valida la sintaxis del compose remoto
	$(SSH) '$(COMPOSE) config --quiet && echo "compose OK"'

logs: ## Últimas 100 líneas (uso: make logs S=app)
	$(SSH) '$(COMPOSE) logs -n 100 $(S)'

shell: ## Shell interactiva dentro del contenedor app
	ssh -t $(REMOTE) 'cd $(REMOTE_DIR) && docker compose exec app bash'

psql: ## psql interactivo contra Silver/Gold
	ssh -t $(REMOTE) 'cd $(REMOTE_DIR) && docker compose exec postgres psql -U $${POSTGRES_USER:-mod1} -d $${POSTGRES_DB:-mod1_practica}'

## --- Observabilidad ---------------------------------------------------------

gpu: ## VRAM y utilización de la RTX 5080 (presupuesto crítico: 16 GB)
	$(SSH) 'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv'

ollama: ## Modelos disponibles en el lab-ollama compartido
	$(SSH) 'curl -s http://127.0.0.1:11434/api/tags'

tunnel: ## Túnel SSH: Postgres remoto → 127.0.0.1:$(PG_PORT) en el Mac (Ctrl-C para cerrar)
	@echo "Postgres de mi-pc disponible en 127.0.0.1:$(PG_PORT) — Ctrl-C para cerrar"
	ssh -N -L $(PG_PORT):127.0.0.1:5432 $(REMOTE)

## --- Aceptación -------------------------------------------------------------

verify: ## Checks de la Definición de Terminado (PRD §8)
	@echo "Ver .claude/skills/acceptance-verify/SKILL.md — checks aún no implementados"
	$(SSH) '$(COMPOSE) exec -T app python -m src.pipeline.verify' || \
		echo "(pendiente: src/pipeline/verify.py es un stub del scaffold)"
