---
name: deploy
description: Desplegar código Mac→mi-pc con flujo GitOps pull-based — commit/push a GitHub desde el Mac, git pull en mi-pc y docker compose up. Aplica la frontera de git (solo código) y respeta el .env manual del servidor. Úsalo para publicar cambios; para operar el servidor sin desplegar usa remote-ops.
---

# deploy — GitOps pull-based Mac→mi-pc

Publica cambios de código en el servidor sin que `mi-pc` sea nunca un origen de commits. Flujo: **Mac (edita+push) → GitHub (`origin`) → mi-pc (`git pull`) → `compose up`**.

## Modelo mental

- **GitHub es la fuente de verdad.** El checkout en `mi-pc:~/augmented/services/MOD1-PRACTICA` es un *deploy target* de solo lectura: **solo hace `git pull`, nunca commit/push**.
- **Git lleva solo código.** Datos (`data/`, `.index`, volúmenes PG) y secretos (`.env`) jamás entran a git ni viajan en el deploy.
- **El `.env` del servidor es sagrado.** Existe solo en mi-pc, editado a mano. Este skill **nunca** lo crea, copia ni sobreescribe.

## Precondiciones

- `origin` apunta a GitHub y el branch de trabajo tiene upstream (`git push -u` ya hecho, o hazlo la primera vez).
- El repo ya está clonado en `mi-pc:~/augmented/services/MOD1-PRACTICA` y su `.env` ya existe.
- Working tree del Mac limpio salvo lo que vas a commitear intencionalmente.

## Procedimiento

### 1. Preparar y publicar desde el Mac
```bash
git status                    # revisa qué entra; NADA de data/ ni .env
git add -A && git commit -m "<mensaje>"
git push                      # a origin (GitHub)
```
Verifica que `.gitignore` excluye `data/`, `*.index`, `.env`, `__pycache__/`, volúmenes. Si algún dato o secreto aparece en `git status`, **detente y corrige `.gitignore` antes de commitear.**

### 2. Traer y levantar en mi-pc (pull-based)
```bash
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA \
  && git fetch --all --prune \
  && git reset --hard @{u} \
  && git log -1 --oneline'
```
> `git reset --hard @{u}` garantiza que el servidor quede **idéntico** al remoto (deploy determinista), descartando cualquier drift local accidental. Es seguro **porque el servidor nunca debe tener cambios propios**; si los tuviera, es un error de proceso que este comando expone.

Luego reconstruye/levanta:
```bash
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA \
  && docker compose config --quiet \
  && docker compose up -d --build \
  && docker compose ps'
```

### 3. Verificar
- `docker compose ps` → servicios `running`/`healthy`.
- `docker compose logs -n 80` del servicio cambiado → sin errores de arranque.
- Si el cambio afecta datos/pipeline, corre el skill **acceptance-verify** correspondiente.

## Primer deploy (bootstrap) — solo si aún no existe el remoto

No lo ejecutes sin autorización explícita (crea recursos externos). Pasos de referencia:
```bash
# En el Mac (requiere gh autenticado):
gh repo create <owner>/MOD1-PRACTICA --private --source=. --remote=origin --push
# En mi-pc (clonar una vez; el .env se crea a mano DESPUÉS):
ssh mi-pc 'cd ~/augmented/services && rm -rf MOD1-PRACTICA \
  && git clone git@github.com:<owner>/MOD1-PRACTICA.git MOD1-PRACTICA'
# El usuario crea a mano: mi-pc:~/augmented/services/MOD1-PRACTICA/.env  (desde .env.example)
```

## Guardrails

- **Nunca** hagas `git commit`/`git push` desde `mi-pc`.
- **Nunca** copies el `.env` local al servidor ni al revés (invariante de secretos = manual en mi-pc).
- Si `git reset --hard @{u}` fuera a descartar cambios locales en el servidor, **repórtalo** antes de continuar (indica ruptura del modelo pull-based).
- Publicar a GitHub es una acción hacia afuera: confirma el push si hay dudas sobre qué contiene el diff.

## Rollback

```bash
# Volver al commit previo en el servidor:
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && git reset --hard HEAD~1 && docker compose up -d --build'
```
Preferible: revertir en el Mac (`git revert`), push, y re-desplegar (mantiene GitHub como fuente de verdad).
