# RUNBOOK — Corridas del pipeline

Cómo ejecutar el pipeline **desde tu propia terminal**, sin intermediarios.

Hay **dos cadencias** y las dos hacen falta:

| | comando | cuándo |
|---|---|---|
| Batch diario | `make batch` | cada día tras el cierre de la BMV (15:00 CT) |
| Refresco histórico | `make refresco` | una vez por semana, mercado cerrado |
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

Tarda unos 4 minutos. Corre las seis etapas en orden y **se detiene en la
primera que falle**, para no dejar Gold construido a medias.

Salida esperada (medida el 27-ago-2026, con 67 noticias nuevas):

```
ETAPA        ESTADO      SEGUNDOS
----------------------------------
ingest       OK             103.7
validate     OK               8.3
enrich       OK             104.5
transform    OK               3.6
correlate    OK               0.9
index        OK              17.4
----------------------------------
TOTAL                       238.5
```

Las dos etapas largas son `ingest` (pausa antirrate-limit de yfinance, ~0,6 s
por ticker) y `enrich` (GPU, ~1,5 s por noticia). `validate` bajó de 214 s a
8 s el 26-ago, cuando dejó de revalidar todo Bronze en cada corrida: ahora
procesa solo los lotes que no ha visto. Si vuelve a subir a minutos, es señal
de que algo está reprocesando lotes antiguos.

**Solo hay que hacerlo después del cierre de la BMV (15:00 hora de Ciudad de
México).** Antes de esa hora el batch se niega a correr, porque ingerir con el
mercado abierto trae una vela incompleta del día que contamina el cálculo de
retornos. Si lo intentas verás:

```
[batch] ABORTADO — son las 13:21 CT y la BMV cierra a las 15:00.
```

Eso **no** es un error: es el guardia funcionando.

> El horario y la fecha con la que se archiva cada lote se calculan en **hora de
> Ciudad de México**, no con el reloj del contenedor (que va en UTC). Antes del
> 26-ago-2026 no era así, y cualquier corrida posterior a las 18:00 quedaba
> archivada bajo la fecha del día siguiente.

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

> **`historial` no es una bitácora de "hubo corrida".** Solo escribe línea
> cuando ejecutas `make batch`. Si corriste etapas sueltas (`make ingest`,
> `make validate`), ese día no aparece aquí aunque sí se ingiriera. Para saber
> qué días tienen datos, la fuente fiable es `make bronze`, no el historial.

---

## La corrida SEMANAL (no la olvides)

Además del batch diario hay una tarea semanal. Corre cuando quieras, con el
mercado cerrado — un fin de semana va bien:

```bash
make refresco
```

Tarda unos 25 segundos y no toca las noticias: solo vuelve a descargar el
histórico completo de precios y macro, y lo carga.

**Por qué hace falta.** La corrida diaria trae solo los últimos 10 días de
precios, que es todo lo que puede haber cambiado. Pero cuando una emisora paga
un dividendo o hace un split, Yahoo **recalcula `Adj Close` hacia atrás en toda
la serie**, y `transform` construye sobre esa columna el retorno diario, las
medias móviles y los múltiplos P/U y P/VL. Sin el refresco, la parte antigua de
la serie se queda con el ajuste viejo y queda una discontinuidad **que ningún
error señala**: las tablas se ven perfectamente normales.

**Por qué semanal y no mensual.** Las 16 emisoras generan del orden de 25
dividendos y splits al año — uno cada dos semanas. Espaciarlo más significa
arrastrar reajustes sin aplicar la mayor parte del tiempo, que es justo lo que
el refresco existe para evitar.

Salida esperada:

```
yahoo_finance    OK      46628 reg → market/yahoo_finance/2026-08-26/...
banxico          OK      16208 reg → market/banxico/2026-08-26/...
...
yahoo_finance       0     46427         201
```

`0` filas nuevas y decenas de miles de actualizadas es lo **correcto**: quiere
decir que el histórico ya estaba completo y solo se refrescaron los ajustes.

---

## El brief ejecutivo (semanal)

```bash
make brief
```

Escribe `data/briefs/YYYY-MM-DD_sectorial.md` y cuesta unos **$0,08**. Es la
única etapa que llama a un modelo comercial; todo lo demás corre en local (ver
la política FinOps de `CLAUDE.md`, que documenta por qué se autoriza y con qué
límites).

**Antes de gastar, mira el contexto:**

```bash
make brief ARGS=--dry-run     # no llama al modelo, no cuesta nada
```

**Control de gasto.** Hay tres capas, y conviene saber cuál es cuál:

| capa | qué corta | dónde |
|---|---|---|
| `MAX_LLAMADAS_POR_CORRIDA` | un bucle dentro de una corrida | código |
| Tope mensual ($5 de aviso en $2) | la deriva a lo largo del mes | código |
| Techo del workspace ($20) | red de seguridad | consola de Anthropic |

```bash
make brief ARGS=--gasto       # historial y acumulado del mes
```

Si alguna vez salta el tope de $5, **no lo subas sin mirar**: con cadencia
semanal lo normal son ~$0,32 al mes, así que alcanzarlo significa que algo
cambió — creció el contexto, o se está llamando más de la cuenta.

> El brief **describe** dónde cotiza cada emisora; no recomienda comprar ni
> vender. El backtest de F3 midió que estas señales no anticipan retorno, así
> que presentarlas como oportunidad contradiría la evidencia del propio
> sistema.

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
`make batch` después de arreglar la causa, lo ya procesado no se duplica.
`enrich` solo toma lo pendiente, y `validate` se salta los lotes que ya cargó.

> Ojo con leer el `filas_nuevas` de `validate` como prueba de idempotencia.
> Desde el 26-ago la etapa salta los lotes ya procesados, así que un 0 puede
> significar «el UPSERT no duplicó» o «no había nada que procesar», que no es
> lo mismo. Por eso el mensaje dice `filas_nuevas = N sobre lotes nuevos`.
> La comprobación de verdad la hace `make verify`, que internamente corre
> `validate --todo` y revalida Bronze entero.

---

## Problemas conocidos

### `lab-ollama` caído tras reiniciar el servidor — ya no requiere intervención manual

**Síntoma (antes del 14-ago-2026):** el batch abortaba en `enrich` con
`Cannot connect to host host.docker.internal:11434`, y `mod1-app` aparecía
*unhealthy*.

**Causa:** los contenedores con GPU pueden arrancar antes de que el driver de
NVIDIA esté cargado y mueren con `nvml error: driver not loaded`. La política
de reinicio de Docker no los recupera, porque el fallo ocurre antes de que el
contenedor llegue a crearse.

**Solución (opción C, adoptada tras el diagnóstico de la semana 10–14 ago):**
`make batch` ahora comprueba `lab-ollama` **antes** de cada corrida y, si no
responde, lo arranca y espera hasta 30s a que esté sano — sin que tengas que
hacer nada. Verás esto en la salida si ocurrió:

```
[batch] lab-ollama no responde -- probable carrera de arranque tras reinicio del host, arrancandolo...
[batch] lab-ollama recuperado
```

Si el arranque automático fallara (poco probable — el driver tarda segundos,
no minutos, en cargar), el mismo bloque lo dice explícitamente y aborta antes
de tocar `enrich`; en ese caso, el diagnóstico manual sigue siendo:

```bash
ssh mi-pc 'docker start lab-ollama'
ssh mi-pc 'curl -s http://127.0.0.1:11434/api/tags | head -c 120'   # debe responder JSON
make batch                                                          # reintentar
```

`lab-ollama` es un servicio **compartido** con otros proyectos del laboratorio
(ADR-5). Este mecanismo solo lo arranca si está caído — nunca toca su
configuración ni su compose.

---

## Comandos útiles sueltos

```bash
make ps                       # estado de los contenedores
make logs S=app               # últimas 100 líneas del contenedor de la app
make verify                   # los 17 checks de la Definición de Terminado (§8)
make calidad                  # vigilancia de calidad de datos (pregunta distinta, ver abajo)
make test                     # las pruebas unitarias (no tocan red ni base)
make gpu                      # VRAM y uso de la RTX 5080
make ollama                   # modelos cargados en el lab-ollama compartido
make bronze                   # inventario de lotes en Bronze (qué días tienen datos)
make refresco                 # refresco histórico SEMANAL de precios y macro
make brief                    # brief ejecutivo semanal por sector (F4)
make brief ARGS=--dry-run     # arma el contexto SIN llamar al modelo ni gastar
make brief ARGS=--gasto       # historial de coste del brief y tope del mes
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

**Reconstruir Silver desde cero.** Silver se regenera entero desde Bronze, pero
`validate` a secas ya no sirve para eso: se saltaría todos los lotes por estar
ya registrados. Hay que forzar la pasada completa:

```bash
make validate ARGS=--todo
```

Es lo que toca correr después de cambiar un contrato o una regla de validación,
porque las filas que entraron bajo la regla vieja no las alcanza el UPSERT si
dejan de satisfacer el contrato nuevo, y quedarían como residuo silencioso.
Tarda unos 3 minutos frente a los 8 segundos de la corrida normal.

---

## `verify` y `calidad` responden preguntas distintas

No son lo mismo y conviene no confundirlos:

| | pregunta | qué significa un fallo |
|---|---|---|
| `make verify` | ¿está construido lo que se prometió? (PRD §8) | falta funcionalidad |
| `make calidad` | ¿los datos de dentro son creíbles? | una fuente externa metió un dato malo |

Un dato sospechoso de Yahoo **no** significa que el sistema esté sin terminar,
por eso `verify` sigue en 17 checks y no crece con esto.

`calidad` usa tres severidades:

- **PROBLEMA** — dato demostrablemente incorrecto. Sale con código 1.
- **SOSPECHA** — patrón improbable que merece una mirada humana. Puede ser
  legítimo; sale con código 0.
- **OK** — sin señales.

Cada check nació de un defecto que **estuvo en producción sin que nadie lo
viera** y se encontró por casualidad. Si alguno empieza a avisar siempre,
recalíbralo o quítalo: un check que siempre avisa deja de leerse y gasta la
atención que debía proteger.

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
- **Saltarte el `make refresco` semanal.** No falla nada visible si lo omites:
  simplemente los múltiplos de la parte antigua de cada serie van quedando
  desajustados tras cada dividendo, sin ningún error que lo delate.
