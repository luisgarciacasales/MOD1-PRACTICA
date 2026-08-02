# AGENTS.md — Orquestador (opencode y agentes compatibles)

> Arnés *lean* para **MOD1-PRACTICA / AI Engineering Lab — Analítica Avanzada de Equity BMV**.
> Este archivo **no contiene procedimientos**: enruta a los skills en `.agents/skills/`.
> **Antes de ejecutar una tarea, abre y sigue el skill correspondiente** (`.agents/skills/<skill>.md`).
> Contexto de producto: `docs/PRD.md`. Constitución y decisiones: `docs/HARNESS.md`.

## Qué es esto en una línea

Motor de analítica NLP + mercado sobre arquitectura Medallón (Bronze→Silver→Gold) contenerizada, que se **desarrolla en el Mac** y se **ejecuta en el servidor Linux `mi-pc`** (Ubuntu 26.04, RTX 5080) vía Docker Compose.

## Invariantes (obligatorias — no negociables)

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

Cada skill es un documento autocontenido. **Léelo completo antes de actuar** y respeta sus precondiciones e invariantes.

| Skill | Documento | Úsalo cuando… |
| --- | --- | --- |
| **remote-ops** | `.agents/skills/remote-ops.md` | Ejecutar cualquier cosa en `mi-pc`: `docker compose`, `exec`, logs, salud, `nvidia-smi`, túneles SSH. |
| **deploy** | `.agents/skills/deploy.md` | Llevar código Mac→mi-pc: commit/push, `git pull` remoto y `compose up`. |
| **medallion-pipeline** | `.agents/skills/medallion-pipeline.md` | Operar/razonar Bronze→Silver→Gold→FAISS con la semántica del PRD. |
| **data-contracts** | `.agents/skills/data-contracts.md` | Autorar/validar contratos Pydantic y `silver_dead_letters`. |
| **acceptance-verify** | `.agents/skills/acceptance-verify.md` | Comprobar la "Definición de Terminado" (§8) como checks ejecutables. |
| **scaffold-service** | `.agents/skills/scaffold-service.md` | Crear/extender la estructura del servicio medallón. |

> Nota: los archivos en `.agents/skills/` son symlinks a la fuente canónica en `.claude/skills/<skill>/SKILL.md`. Editar cualquiera de los dos edita el mismo contenido.

## Reglas de operación del agente

- Antes de tocar el servidor, **lee el skill relevante** y respeta sus precondiciones.
- Operaciones destructivas (`down -v`, `rm`, `DROP`, borrar datos) requieren confirmación explícita del usuario.
- Si una acción viola una invariante, **detente y avísalo** en vez de continuar.
- Reporta resultados con fidelidad (si un check falla, muéstralo con su salida).
