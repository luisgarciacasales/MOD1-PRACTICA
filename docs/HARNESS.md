# HARNESS.md — Arquitectura del arnés y decisiones

Este documento explica **cómo está construido el arnés** de MOD1-PRACTICA y **por qué**. Los orquestadores (`CLAUDE.md`, `AGENTS.md`) enrutan; los skills ejecutan; este archivo justifica.

## Principio de diseño

**Orquestador lean + skills como única fuente de procedimiento.**
`CLAUDE.md` y `AGENTS.md` contienen solo: qué es el proyecto, las invariantes, los facts de entorno y una tabla de enrutamiento a skills. **Ningún procedimiento vive en el orquestador.** Cada skill es autocontenido, idempotente y verificable.

## Compatibilidad multi-agente

| Agente | Lee | Skills |
| --- | --- | --- |
| Claude Code | `CLAUDE.md` (+ auto-descubre `.claude/skills/`) | `.claude/skills/<skill>/SKILL.md` (fuente canónica, con frontmatter) |
| opencode (y agentes que respetan AGENTS.md) | `AGENTS.md` | `.agents/skills/<skill>.md` |

**DRY vía symlink:** `.agents/skills/<skill>.md` es un **symlink** a `.claude/skills/<skill>/SKILL.md`. Una sola fuente de verdad, presente en ambos lugares; editar cualquiera edita el mismo contenido. Git preserva los symlinks y funcionan igual en el Mac y en mi-pc.

## Catálogo de skills

1. **remote-ops** — primitivas sobre `mi-pc` (docker compose, exec, logs, salud, nvidia-smi, túneles).
2. **deploy** — GitOps pull-based Mac→GitHub→mi-pc.
3. **medallion-pipeline** — semántica y operación de Bronze→Silver→Gold→FAISS.
4. **data-contracts** — contratos Pydantic + dead-letter queue.
5. **acceptance-verify** — Definición de Terminado (§8) como checks ejecutables.
6. **scaffold-service** — andamiaje del proyecto compatible con las convenciones del servidor.

## Decisiones (ADR breve)

- **ADR-1 · Este repo es el código.** Se desarrolla en el Mac y se ejecuta en `mi-pc:~/augmented/services/MOD1-PRACTICA`. El arnés vive junto al código.
- **ADR-2 · GitHub como source of truth, deploy pull-based.** `mi-pc` solo hace `git pull`; nunca commitea. Motivos: reproducibilidad/CI, trazabilidad (alineado con la filosofía de inmutabilidad del PRD), backup off-machine, multi-agente. CI (GitHub Actions) queda como hook futuro.
- **ADR-3 · Frontera de git estricta.** Git = código/compose/config/prompts/docs. Datos y secretos jamás. Los datos viven en volúmenes/`data/` en mi-pc; los secretos en `.env` manual.
- **ADR-4 · Secretos manuales en mi-pc.** `.env` se crea/edita a mano una vez en el servidor desde `.env.example`. El skill deploy nunca lo toca. Simple y sin dependencias; upgrade a SOPS/pass/Docker secrets posible en el futuro.
- **ADR-5 · Reusar `lab-ollama` por host-gateway.** MOD1-PRACTICA no levanta Ollama propio; llama a `http://host.docker.internal:11434`. Motivo: una sola copia de modelos en la GPU (VRAM 16 GB es riesgo alto del PRD) y cero cambios al servicio compartido existente. Ruta de graduación documentada: red externa `augmented-net` cuando crezca la intercomunicación.
- **ADR-6 · Seguridad = loopback + túnel SSH.** Convención heredada de los servicios vecinos; nunca exponer puertos a `0.0.0.0` sin justificación en el compose.
- **ADR-7 · Alcance por fases.** Esta ronda entrega **solo el arnés** (sin código de sistema ni scaffold). El scaffold se materializa después, guiado por `scaffold-service`.

## Entorno verificado (2026-08-01)

- `ssh mi-pc` → host `jose-gaming`, Ubuntu 26.04 LTS, Docker 29.6.1, Compose v5.2.0, GPU NVIDIA RTX 5080 (16 GB).
- Servicios en `~/augmented/services/`: `MOD1-PRACTICA` (target), `lab-ollama` (`:11434`, GPU), `lab-nlp-fomc`, `lab-pytorch`, `fomc-bias`, `ollama`.

## Cómo evolucionar el arnés

- **Nuevo procedimiento recurrente** → nuevo skill en `.claude/skills/` + symlink en `.agents/skills/` + fila en las tablas de `CLAUDE.md`/`AGENTS.md`.
- **Cambio de invariante** → actualiza las invariantes en *ambos* orquestadores y añade un ADR aquí.
- Mantén los orquestadores lean: si un procedimiento crece en `CLAUDE.md`/`AGENTS.md`, muévelo a un skill.
