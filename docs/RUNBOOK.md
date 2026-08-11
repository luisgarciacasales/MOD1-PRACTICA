# RUNBOOK — Corrida diaria

Cómo ejecutar el batch diario **desde tu propia terminal**, sin intermediarios.
Vale igual en la terminal integrada de VS Codium, en Terminal.app o en iTerm: es
un shell normal.

No hace falta conectarse al servidor a mano. El `Makefile` detecta que existe el
alias SSH `mi-pc` y envía cada comando allí solo; tú trabajas siempre desde el
repo en el Mac.

---

## Antes de empezar (una vez, para comprobar que todo está en su sitio)

```bash
cd ~/trabajo/augmented-humans/mod1-practica
make where
```

Debe responder:

```
modo            : remoto (detectado)
contexto        : remoto — los comandos viajan a mi-pc por SSH
```

Si dice `local`, el alias `mi-pc` no se está resolviendo y nada funcionará: revisa
tu `~/.ssh/config`.

---

## La corrida diaria

**Tres comandos, en este orden.**

### 1. Publica lo que haya pendiente y actualiza el servidor

```bash
make deploy
```

Hace `git pull` en `mi-pc`, levanta los servicios y muestra su estado. Si no has
cambiado código, igual conviene: confirma que los contenedores están arriba.

Los dos deben aparecer como `healthy`:

```
mod1-app        Up ... (healthy)
mod1-postgres   Up ... (healthy)
```

> Si `mod1-app` sale **unhealthy**, casi siempre significa que `lab-ollama` está
> caído. Ve a *Problemas conocidos* más abajo.

### 2. Ejecuta el batch

```bash
make batch
```

Tarda entre 3 y 4 minutos. Corre las seis etapas en orden y **se detiene en la
primera que falle**, para no dejar Gold construido a medias.

Salida esperada:

```
ETAPA        ESTADO      SEGUNDOS
----------------------------------
ingest       OK              45.5
validate     OK              57.7
enrich       OK              98.9
transform    OK               0.3
correlate    OK               0.5
index        OK              20.4
----------------------------------
TOTAL                       223.3
```

**Solo hay que hacerlo después del cierre de la BMV (15:00 hora de Ciudad de
México).** Antes de esa hora el batch se niega a correr, porque ingerir con el
mercado abierto trae una vela incompleta del día que contamina el cálculo de
retornos. Si lo intentas verás:

```
[batch] ABORTADO — son las 13:21 CT y la BMV cierra a las 15:00.
```

Eso **no** es un error: es el guardia funcionando.

### 3. Revisa la evolución entre días

```bash
make historial
```

Una línea por corrida, con tiempos y volúmenes:

```
2026-08-10T21:23:57-06:00 OK  total=223s ingest=46s ... | news=476 gold=476 corr=142 proxy=20 ...
```

Es lo que permite ver si el sistema se comporta igual día a día. Un `total`
creciente **no** es mala señal por sí solo: mira si `news` creció en la misma
proporción.

---

## Cómo saber si fue bien

El código de salida distingue tres casos. Para verlo:

```bash
make batch; echo "salida: $?"
```

| Código | Significado | Qué hacer |
|---|---|---|
| **0** | Las seis etapas completaron | Nada |
| **1** | Una etapa falló y la cadena se detuvo | Ver *Cuando algo falla* |
| **2** | No es horario: la BMV sigue abierta | Esperar a las 15:00 CT |

---

## Cuando algo falla

El batch dice en qué etapa se detuvo y deja el detalle completo en un log.

```bash
# Últimas líneas del log de la corrida más reciente
ssh mi-pc 'cd ~/augmented/services/MOD1-PRACTICA && tail -40 "$(ls -t data/logs/batch_*.log | head -1)"'
```

**Reintentar es seguro.** Todas las etapas son idempotentes: si vuelves a correr
`make batch` después de arreglar la causa, lo ya procesado no se duplica —
`validate` reportará `filas_nuevas = 0` y `enrich` solo tomará lo pendiente.

---

## Problemas conocidos

### `lab-ollama` caído tras reiniciar el servidor

**Síntoma:** el batch aborta en `enrich` con
`Cannot connect to host host.docker.internal:11434`, y `mod1-app` aparece
*unhealthy*.

**Causa:** los contenedores con GPU arrancan antes de que el driver de NVIDIA
esté cargado y mueren con `nvml error: driver not loaded`. La política de
reinicio de Docker no los recupera, porque el fallo ocurre antes de que el
contenedor llegue a crearse.

**Solución:**

```bash
ssh mi-pc 'docker start lab-ollama'
ssh mi-pc 'curl -s http://127.0.0.1:11434/api/tags | head -c 120'   # debe responder JSON
make batch                                                          # reintentar
```

`lab-ollama` es un servicio **compartido** con otros proyectos del laboratorio.
Arrancarlo restaura su estado previsto; no cambies su configuración sin decidirlo
antes.

---

## Comandos útiles sueltos

```bash
make ps                       # estado de los contenedores
make logs S=app               # últimas 100 líneas del contenedor de la app
make verify                   # los 17 checks de la Definición de Terminado (§8)
make test                     # las pruebas unitarias (no tocan red ni base)
make gpu                      # VRAM y uso de la RTX 5080
make ollama                   # modelos cargados en el lab-ollama compartido
make bronze                   # inventario de lotes en Bronze
make search Q="tasas de Banxico"     # búsqueda semántica
make psql                     # consola SQL interactiva contra Silver/Gold
make help                     # todos los targets
```

### Etapas por separado

Si necesitas correr solo una parte —por ejemplo, reingerir sin gastar GPU:

```bash
make ingest
make validate
make batch ARGS="--hasta transform"    # la cadena, pero deteniéndose antes
```

---

## Lo que NO debes hacer

- **`docker compose down -v`** en el servidor: borra el volumen de PostgreSQL con
  todo Silver y Gold. Bronze sobreviviría, pero habría que reconstruir el resto.
- **Commitear desde `mi-pc`.** El servidor solo hace `git pull`. Todo cambio nace
  en el Mac (invariante 2).
- **Copiar tu `.env` al servidor o al revés.** Vive solo allí, editado a mano
  (invariante 3).
- **Correr el batch antes de las 15:00 CT** con `--ignorar-horario` sin motivo: la
  vela del día estaría incompleta.
