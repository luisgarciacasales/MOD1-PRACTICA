# Makefile — atajos para operar MOD1-PRACTICA.
#
# Se adapta solo a TRES contextos, sin configuración:
#
#   local    máquina cualquiera con el repo y Docker. Todo corre aquí mismo.
#            Es el caso de quien clona el repo por primera vez.
#   remoto   hay un alias SSH real para $(REMOTE): los comandos viajan allí.
#   servidor se está ejecutando EN el propio servidor de despliegue.
#
# La detección NO pregunta "¿soy tal máquina?" sino "¿existe a dónde ir?".
# La versión anterior comprobaba el hostname y caía a `ssh mi-pc` en cualquier
# otro caso, así que en una máquina ajena TODOS los targets fallaban con
# "Could not resolve hostname mi-pc". Ahora el default es local, que es lo
# único cierto para cualquiera que no sea el autor.
#
# Se puede forzar:  make MODO=local ...   |   make MODO=remoto REMOTE=otro-host ...
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

# `ssh -G <host>` imprime la configuración resuelta. Si existe un bloque
# `Host <host>` en ~/.ssh/config, el `hostname` resuelto difiere del literal;
# si no existe el alias, coincide. Esa diferencia es la señal de que hay un
# destino remoto de verdad y no un nombre que nadie sabe resolver.
ALIAS := $(shell ssh -G $(REMOTE) 2>/dev/null | awk '/^hostname /{print $$2}')

ifeq ($(HOST),$(SERVER_HOSTNAME))
  MODO_AUTO := servidor
else ifeq ($(ALIAS),)
  MODO_AUTO := local
else ifeq ($(ALIAS),$(REMOTE))
  MODO_AUTO := local
else
  MODO_AUTO := remoto
endif

MODO ?= $(MODO_AUTO)

ifeq ($(MODO),remoto)
  RUN      := ssh $(REMOTE)
  RUN_T    := ssh -t $(REMOTE)
  DIR      := $(REMOTE_DIR)
  CONTEXTO := remoto — los comandos viajan a $(REMOTE) por SSH
else
  # `sh -c` recibe la misma cadena entre comillas que recibiría ssh, así que
  # los targets se escriben una sola vez y no cambian de forma.
  RUN      := sh -c
  RUN_T    := sh -c
  DIR      := .
  CONTEXTO := $(MODO) — todo se ejecuta en esta máquina
endif

COMPOSE := cd $(DIR) && docker compose

# Guardia para targets que no tienen sentido en el servidor de despliegue.
# OJO: $(call) separa argumentos por comas, así que el mensaje que se le pase
# NO debe contener ninguna o quedará truncado en la primera.
define no_en_servidor
	@if [ "$(MODO)" = "servidor" ]; then \
		echo "ERROR: '$@' no se ejecuta en el servidor de despliegue."; \
		echo "  Motivo: $(1)"; \
		exit 1; \
	fi
endef

# Guardia para targets que exigen un destino remoto configurado.
define exige_remoto
	@if [ "$(MODO)" != "remoto" ]; then \
		echo "ERROR: '$@' necesita MODO=remoto (hoy: $(MODO))."; \
		echo "  Motivo: $(1)"; \
		exit 1; \
	fi
endef

.DEFAULT_GOAL := help
.PHONY: help where init deploy push pull up down build ps logs shell psql config tunnel gpu ollama batch historial ingest validate enrich transform correlate index search demo evidencia bronze migrate test verify

help: ## Muestra esta ayuda
	@echo "Contexto detectado: $(CONTEXTO)"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

where: ## Muestra dónde se ejecutarán los comandos y por qué
	@echo "hostname        : $(HOST)"
	@echo "modo            : $(MODO)$(if $(filter $(MODO),$(MODO_AUTO)), (detectado), (forzado; auto sería $(MODO_AUTO)))"
	@echo "contexto        : $(CONTEXTO)"
	@echo "alias SSH       : $(REMOTE) -> $(if $(ALIAS),$(ALIAS),(sin resolver))"
	@echo "directorio      : $(DIR)"
	@echo "prefijo de run  : $(RUN)"

## --- Deploy (Mac → GitHub → mi-pc) -----------------------------------------

push: ## Commitea y publica a GitHub (uso: make push M="mensaje")
	$(call no_en_servidor,invariante 2 — el servidor solo hace git pull y nunca commitea)
	@test -n "$(M)" || (echo "ERROR: usa make push M=\"mensaje de commit\""; exit 1)
	@git status --short
	@git add -A && git commit -m "$(M)" && git push

pull: ## git pull determinista en el destino (nunca commitea — invariante 2)
	$(RUN) 'cd $(DIR) && git fetch --all --prune && git reset --hard @{u} && git log -1 --oneline'

deploy: pull up ps ## pull + up + ps (el .env del servidor NO se toca)

## --- Ciclo de vida ----------------------------------------------------------

init: ## Crea el árbol data/ con el ownership correcto (ANTES del primer up)
	@echo "Creando data/ [$(MODO)] — si no existe, Docker lo crearía como root."
	$(RUN) 'cd $(DIR) && mkdir -p data/bronze/news data/bronze/market \
		data/silver data/gold data/cache data/faiss data/hf_cache && ls -la data'
	@echo "Recuerda: el .env se crea A MANO desde .env.example y nunca entra a git (invariante 3)."

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

tunnel: ## Túnel SSH a Postgres del host remoto (solo MODO=remoto)
	$(call exige_remoto,en local el puerto ya está en 127.0.0.1:5432 sin túnel)
	@echo "Postgres de $(REMOTE) disponible en 127.0.0.1:$(PG_PORT) — Ctrl-C para cerrar"
	ssh -N -L $(PG_PORT):127.0.0.1:5432 $(REMOTE)

## --- Pipeline ---------------------------------------------------------------

batch: ## Corrida diaria completa: las 6 etapas en orden, con log (uso: make batch ARGS=--ignorar-horario)
	$(RUN) '$(COMPOSE) exec -T app python scripts/batch.py $(ARGS)'

historial: ## Una línea por corrida del batch, para ver la estabilidad entre días
	$(RUN) 'cd $(DIR) && tail -20 data/logs/historial.log 2>/dev/null || echo "(sin corridas registradas)"'


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

evidencia: ## Copia el informe de evidencia a docs/ (solo MODO=remoto)
	$(call exige_remoto,en local el informe ya está en data/evidencia_e2e.md)
	scp $(REMOTE):$(REMOTE_DIR)/data/evidencia_e2e.md ./docs/EVIDENCIA_E2E.md
	@echo "Escrito en docs/EVIDENCIA_E2E.md — versionado como entregable"

verify: ## Checks de la Definición de Terminado (PRD §8)
	$(RUN) '$(COMPOSE) exec -T app python -m src.pipeline.verify $(ARGS)'
