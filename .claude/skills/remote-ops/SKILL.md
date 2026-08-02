---
name: remote-ops
description: Ejecutar operaciones en el servidor Linux mi-pc (Ubuntu 26.04, RTX 5080) — ciclo de vida de docker compose, exec a contenedores, logs, salud, nvidia-smi/VRAM y túneles SSH a puertos loopback. Úsalo para cualquier acción sobre mi-pc que no sea un deploy de código.
---

# remote-ops — Operar `mi-pc`

Primitivas para ejecutar y observar procesos en el servidor `mi-pc` sin desplegar código nuevo. Si vas a **llevar código** Mac→servidor, usa el skill **deploy** en su lugar.

## Precondiciones

- `ssh mi-pc` resuelve (alias en `~/.ssh/config` → `jose-gaming`, `100.110.147.125`).
- El proyecto está en `~/augmented/services/MOD1-PRACTICA` con un `compose.yaml` presente.
- `docker` y `docker compose` disponibles en el servidor (v29.6 / v5.2 verificados).

## Convenciones

- **Prefijo remoto.** Todo comando se ejecuta vía SSH. Usa siempre `cd` explícito porque el shell no persiste entre invocaciones:
  ```bash
  ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose ps'
  ```
- **Un comando por línea lógica.** Encadena con `&&` dentro de una sola invocación `ssh`; no dependas de estado de una llamada a la siguiente.
- **Nombre de proyecto compose.** El `name:` del compose fija el prefijo de contenedores/redes. Respétalo al filtrar (`docker ps --filter name=...`).

## Recetas

### Estado y salud
```bash
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose ps'
ssh mi-pc 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose config --quiet && echo "compose OK"'
```

### Ciclo de vida
```bash
# Levantar (build si cambió Dockerfile). Preferir -d.
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose up -d --build'
# Recrear un solo servicio
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose up -d --no-deps --build <servicio>'
# Detener sin borrar volúmenes
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose down'
```
> ⛔ `docker compose down -v` **borra volúmenes** (datos de PostgreSQL, modelos, índice). Requiere confirmación explícita del usuario. Nunca lo ejecutes de forma proactiva.

### Logs y ejecución dentro de contenedores
```bash
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose logs -n 100 <servicio>'
# Ejecutar un pipeline / comando puntual dentro de un servicio
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T <servicio> python -m <modulo>'
# psql (ver skill medallion-pipeline / acceptance-verify para consultas concretas)
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T postgres psql -U <user> -d <db> -c "SELECT 1;"'
```
> Usa `-T` en `exec`/`run` cuando invocas por SSH no interactivo (evita el error "the input device is not a TTY").

### GPU / VRAM (presupuesto crítico: 16 GB)
```bash
ssh mi-pc 'nvidia-smi --query-gpu=memory.used,memory.total,utilization.gpu --format=csv'
# Comprobar que Ollama compartido responde
ssh mi-pc 'curl -s http://127.0.0.1:11434/api/tags | head -c 400; echo'
```
Antes de subir el batch async de 8→16, verifica margen de VRAM aquí.

### Túnel SSH a puertos loopback
Los servicios publican en `127.0.0.1` del servidor; para acceder desde el Mac:
```bash
# Ejemplo: exponer Postgres (5432 remoto) en 5433 local
ssh -N -L 5433:127.0.0.1:5432 mi-pc
# Genérico: -L <puerto_local>:127.0.0.1:<puerto_remoto>
```
Deja el túnel en background solo si el usuario lo pide; ciérralo al terminar.

## Guardrails

- **Nunca** publiques puertos en `0.0.0.0` ni deshabilites el binding loopback sin justificación documentada en el compose.
- **Nunca** levantes un contenedor Ollama propio: usa el `lab-ollama` compartido (`:11434`).
- Operaciones destructivas (`down -v`, `docker volume rm`, `docker system prune`, `rm -rf` en `data/`) requieren confirmación explícita.
- No edites `.env` en el servidor desde aquí (es responsabilidad manual del usuario; ver invariante de secretos).

## Verificación

Tras cualquier `up`, confirma con `docker compose ps` que los servicios están `healthy`/`running` y revisa `logs` en busca de errores de arranque (p. ej. cold start de modelos).
