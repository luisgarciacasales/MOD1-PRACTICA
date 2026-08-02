# README_MAC — operar MOD1-PRACTICA desde el Mac

Cómo trabajar este proyecto desde el Mac sabiendo que **nada se ejecuta aquí**:
el código se edita en el Mac y corre en `mi-pc`. Contexto de producto en
`docs/PRD.md`; decisiones del arnés en `docs/HARNESS.md`.

## Modelo mental en tres líneas

```
Mac (editas)  ──git push──▶  GitHub (fuente de verdad)  ──git pull──▶  mi-pc (ejecuta)
```

`mi-pc` **nunca** commitea. El `.env` **nunca** viaja por git: vive a mano solo
en el servidor. Los datos (`data/`, índice FAISS, volumen de PostgreSQL) **nunca**
entran al repo.

## Puesta en marcha (una sola vez)

```bash
# 1. Clonar el repo en el servidor
ssh mi-pc 'cd ~/augmented/services && git clone <url-del-repo> MOD1-PRACTICA'

# 2. Crear el árbol de datos ANTES del primer up.
#    Si data/ no existe, Docker lo crea como root y el contenedor (que corre con
#    tu UID) no puede escribir en Bronze.
make init

# 3. Crear el .env A MANO en el servidor (invariante 3 — deploy nunca lo toca)
ssh mi-pc
cd ~/augmented/services/MOD1-PRACTICA
cp .env.example .env && nano .env      # POSTGRES_PASSWORD y BANXICO_TOKEN
#    UID/GID deben coincidir con tu usuario en el servidor: comprueba con `id -u` / `id -g`

# 4. Levantar
make up && make ps
```

## Ciclo de trabajo diario

```bash
make push M="lo que cambié"   # commit + push a GitHub desde el Mac
make deploy                   # pull determinista + up + ps en mi-pc
make logs S=app               # ver qué pasó
```

`make deploy` hace `git reset --hard @{u}` en el servidor. Es intencional: deja
mi-pc **idéntico** al remoto y expone cualquier cambio local que alguien hubiera
hecho allí, que sería una ruptura del modelo pull-based.

## Ejecutar el pipeline

Cada etapa se invoca por separado dentro del contenedor `app`:

```bash
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && docker compose exec -T app python -m src.pipeline.ingest'
```

Etapas disponibles: `ingest`, `validate`, `enrich`, `transform`, `correlate`,
`index`, `verify`. Hoy son **stubs del scaffold** — devuelven código 1 y apuntan
al skill que las especifica.

## Acceder a PostgreSQL desde el Mac

El puerto solo escucha en `127.0.0.1` del servidor (invariante 4), así que hace
falta un túnel:

```bash
make tunnel            # deja 127.0.0.1:5433 en el Mac → 5432 en mi-pc; Ctrl-C cierra
# en otra terminal:
psql -h 127.0.0.1 -p 5433 -U mod1 -d mod1_practica
```

O directamente sin túnel: `make psql`.

## Vigilar la GPU

La RTX 5080 tiene **16 GB** y se comparte con `lab-ollama` y `lab-pytorch`. Antes
de subir `NLP_BATCH_SIZE` de 8 a 16:

```bash
make gpu        # memoria usada / total / utilización
make ollama     # qué modelos hay cargados en el lab-ollama compartido
```

## Comandos

`make help` los lista todos.

| Comando | Qué hace |
| --- | --- |
| `make init` | Crea `data/` en el servidor con el ownership correcto |
| `make push M="..."` | Commit + push a GitHub |
| `make deploy` | `pull` + `up` + `ps` en mi-pc |
| `make up` / `down` | Levanta / detiene (sin borrar volúmenes) |
| `make ps` / `logs S=app` | Estado y logs |
| `make shell` / `psql` | Shell en `app` / psql en Silver-Gold |
| `make config` | Valida la sintaxis del compose remoto |
| `make tunnel` | Túnel SSH a PostgreSQL |
| `make gpu` / `ollama` | VRAM y catálogo de modelos |
| `make verify` | Checks de la Definición de Terminado (PRD §8) |

## Lo que NO debes hacer

- `docker compose down -v` — borra el volumen de PostgreSQL. Requiere decisión
  explícita, no es parte de ningún flujo normal.
- Commitear desde `mi-pc`. Rompe la invariante 2.
- Copiar tu `.env` al servidor o al revés. Se edita a mano allí, y punto.
- Levantar un Ollama propio. Se reusa `lab-ollama` (ADR-5); una segunda copia de
  los modelos en la GPU es el riesgo alto nº2 del PRD.
