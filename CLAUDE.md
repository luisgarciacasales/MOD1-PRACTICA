# CLAUDE.md — Orquestador (Claude Code)

> Arnés *lean* para **MOD1-PRACTICA / AI Engineering Lab — Analítica Avanzada de Equity BMV**.
> Este archivo **no contiene procedimientos**: solo enruta a los skills. El detalle vive en `.claude/skills/`.
> Contexto de producto: `docs/PRD.md`. Decisiones y constitución del arnés: `docs/HARNESS.md`.

## Qué es esto en una línea

Motor de analítica NLP + mercado sobre arquitectura Medallón (Bronze→Silver→Gold) contenerizada, que se **desarrolla en el Mac** y se **ejecuta en el servidor Linux `mi-pc`** (Ubuntu 26.04, RTX 5080) vía Docker Compose.

## Invariantes (léelas antes de actuar — no negociables)

1. **Frontera de git.** Git versiona **solo** código, `compose.yaml`, config, prompts y docs. **NUNCA** datos (`data/bronze|silver|gold`, índice `.index` de FAISS, volúmenes de PostgreSQL) ni secretos (`.env`). Ver `.gitignore`.
2. **Deploy pull-based.** `mi-pc` **solo hace `git pull`**; jamás se commitea desde el servidor. Todo cambio nace en el Mac → push → `git pull` en mi-pc. → skill **deploy**.
3. **Secretos manuales.** `.env` vive **solo** en `mi-pc:~/augmented/services/MOD1-PRACTICA/.env`, editado a mano una vez. El repo trae `.env.example`. **deploy nunca crea ni sobreescribe `.env`.**
4. **Red = loopback + túnel SSH.** Todo puerto se publica en `127.0.0.1` y se accede por túnel SSH. Nunca exponer a `0.0.0.0` sin justificación documentada en el compose.
5. **Ollama compartido.** Reusar el contenedor `lab-ollama` (`:11434`) vía `host.docker.internal:11434` (`extra_hosts: host-gateway`). **No** levantar un Ollama propio (la VRAM de 16 GB es un riesgo alto del PRD).
6. **Semántica del PRD.** Bronze inmutable · contrato Pydantic con `silver_dead_letters` · cargas idempotentes `INSERT ... ON CONFLICT` (reproceso ⇒ `filas_nuevas = 0`) · ingesta fail-soft por fuente · JOIN temporal con calendario **XMEX** · **proxy ticker** para fintechs sin cotización.
7. **Presupuesto GPU.** Modelos 7–8B cuantizados a 4-bit; inferencia async en lotes de 8.
8. **Idioma.** Documentación, prompts y comentarios en **español**.

## Entorno (facts verificados)

- Servidor: `ssh mi-pc` → host `jose-gaming`, Ubuntu 26.04 LTS, Docker 29.6.1, Compose v5.2.0, GPU RTX 5080 (16 GB).
- Ruta del proyecto en el servidor: `~/augmented/services/MOD1-PRACTICA`.
- Servicios vecinos en `~/augmented/services/`: `lab-ollama` (GPU, `:11434`), `lab-nlp-fomc`, `lab-pytorch`, `fomc-bias`.
- Remote git: GitHub (`origin`) como source of truth.

## Skills — enrutamiento

Claude descubre automáticamente los skills en `.claude/skills/`. Usa el que corresponda **antes** de ejecutar; no improvises procedimientos que ya viven en un skill.

| Skill | Úsalo cuando… |
| --- | --- |
| **remote-ops** | Ejecutar cualquier cosa en `mi-pc`: `docker compose` up/down/build/logs, `exec` a contenedores, salud, `nvidia-smi`/VRAM, abrir túneles SSH a puertos loopback. |
| **deploy** | Llevar código Mac→mi-pc: commit/push, `git pull` remoto y `compose up`. Aplica la frontera de git y respeta el `.env` del servidor. |
| **medallion-pipeline** | Operar/razonar las etapas Bronze→Silver→Gold→FAISS con la semántica del PRD (batch, idempotencia, async×8, XMEX, proxy). |
| **data-contracts** | Autorar/validar contratos Pydantic y convenciones de `silver_dead_letters` (MISSING_ENTITY, bypass macro). |
| **acceptance-verify** | Comprobar la "Definición de Terminado" (§8 del PRD) como checks ejecutables. |
| **scaffold-service** | Crear/extender la estructura del servicio medallón compatible con las convenciones de `mi-pc`. |

## Reglas de operación del agente

- Antes de tocar el servidor, **lee el skill relevante** y respeta sus precondiciones.
- Operaciones destructivas (`down -v`, `rm`, `DROP`, borrar datos) requieren confirmación explícita del usuario.
- Si una acción viola una invariante, **detente y avísalo** en vez de continuar.
- Reporta resultados con fidelidad (si un check falla, muéstralo con su salida).

## FinOps y Optimización de Inferencia (Agentes y Costos)

*   **Enrutamiento de Modelos (Model Routing):** 
    *   **Inferencia Local (Costo Cero):** Todo el procesamiento masivo de texto, NLP, clasificación de tonos, chunking y análisis de discursos de la FED debe resolverse estrictamente de forma local usando Ollama (`OLLAMA_BASE_URL` en `mi-pc` con la RTX 5080). Está prohibido consumir tokens de API comerciales en tareas por lotes.
    *   **Desarrollo de Código (Bajo Costo):** Utiliza modelos económicos (como DeepSeek a través de Opencode) para la generación rutinaria de scripts de Python, pruebas y refactorizaciones.
    *   **Arquitectura / Debugging Crítico (Claude):** Reserva la capacidad de Claude Platform exclusivamente para diseño arquitectónico de alto nivel, resolución de bloqueos complejos de lógica o auditorías donde los modelos económicos hayan fallado.
*   **Gestión de Contexto y Ventanas:** 
    *   Para evitar truncamientos silenciosos (como el error histórico del 31% del corpus detectado en agosto de 2026), toda llamada a Ollama que procese discursos largos debe configurar explícitamente `num_ctx=16384`.
    *   No envíes historiales de chat masivos ni logs innecesarios en las llamadas de API para optimizar el consumo de tokens y proteger el presupuesto mensual.
