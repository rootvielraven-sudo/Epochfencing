# Fallos y Resiliencia — pipeline distribuido de reconstrucción de facturas + VERA

Pipeline distribuido (Ray) que reconstruye facturas degradadas por OCR y **sobrevive a la
caída de nodos sin perder ni duplicar**. Al final, un verificador determinista (**VERA**)
actúa como **compuerta**: solo lo que avala entra al PDF como *verificado*; lo demás entra
con su OCR crudo y **marcado** (`DEGRADED`).

## En palabras simples (glosario)

> Si algún término te resulta nuevo, acá está en criollo. El resto del README asume estas
> definiciones. Si ya los conocés, saltealo.

**El problema**
- **OCR**: el software que "lee" una imagen y la convierte en texto. A baja resolución se
  equivoca: parte palabras, confunde caracteres, rompe números. El resultado es una **factura
  degradada**.
- **Reconstrucción (LLM-GSR)**: usar un modelo de lenguaje para *recuperar con fundamento* el
  texto original a partir del degradado. Como cuando leés un cartel borroso y tu cabeza
  completa las letras que faltan.
- **Ground truth** (verdad de referencia): el dato correcto de verdad. Lo conocemos porque
  nosotros generamos las facturas. Sirve para *corregir el examen, no para rendirlo*: el
  modelo **nunca** lo ve.
- **Alucinación**: cuando el modelo, en vez de reconstruir, **inventa** un valor que *parece*
  plausible pero no está en la fuente. Es el fallo más peligroso porque pasa desapercibido.

**Sistemas distribuidos y resiliencia** (repartir el trabajo entre muchos procesos y
sobrevivir a que alguno falle)
- **Nodo / worker**: uno de los procesos que hace el trabajo. **Ray** es la librería que los
  coordina y reparte las facturas.
- **Caída (crash)**: un worker se muere a mitad de una factura. El objetivo es no perderla.
- **Lease** (arrendamiento): un "préstamo con vencimiento" de la factura a un worker. Si no la
  termina y confirma antes de que venza, otro worker la agarra. Como una reserva que se libera
  sola si no la usás.
- **Zombi**: un worker que se daba por muerto pero seguía vivo y escribe **tarde**, con datos
  viejos. El peligro: que pise el resultado bueno.
- **Epoch**: un número de intento que sube cada vez (1, 2, 3…). Dice "esta escritura es del
  intento N".
- **Epoch fencing** (lo que da nombre al repo): la regla de que *solo se acepta la escritura
  del epoch más nuevo*. El zombi llega con un epoch viejo → se rechaza. (*fencing* = "poner
  una cerca" alrededor del dato bueno.)
- **Durable**: guardado en disco de verdad, sobrevive a un reinicio (≠ en memoria, que se
  pierde al caer).
- **Atómico / TOCTOU**: una operación **atómica** pasa entera o no pasa, sin estados a medias.
  **TOCTOU** (*time-of-check to time-of-use*) es el bug de "chequeo y después uso, pero entre
  medio algo cambió"; lo evitamos uniendo chequeo + escritura en un solo paso atómico.
- **at-least-once / exactly-once effect**: el trabajo puede ejecutarse *más de una vez* (por
  reintentos), pero el **resultado final** queda como si se hubiera hecho *exactamente una
  vez*, sin duplicados. Es la garantía honesta que da este sistema.

**Verificación (VERA)**
- **VERA**: un verificador **determinista** (reglas fijas; mismo dato de entrada → mismo
  veredicto). **No es otra IA**: por eso sus decisiones son auditables y repetibles.
- **Compuerta (gate)**: VERA decide quién pasa. Lo que avala entra al PDF como *verificado*;
  lo que no, entra marcado como `DEGRADED` (con su OCR crudo, visible pero sin "sello").
- **Cobertura**: ¿cada valor que reporta el modelo *aparece anclado* en el texto fuente? Si no
  aparece, es probable alucinación (veredicto `INVENTED`).
- **Cálculo**: ¿las cuentas cierran? (p. ej. Σ líneas = total, IVA coherente). Atrapa números
  bien formados pero que **no cuadran** = *corrupción silenciosa*.
- **Abstención** / `[UNRECOVERABLE]`: que el modelo diga "no sé" en vez de inventar.
  Abstenerse es mejor que alucinar.
- **Fuente privilegiada vs. independiente**: si VERA verifica contra un texto *derivado de la
  verdad*, se está "haciendo trampa sola" (es **circular**) y da 100 %. Contra una fuente
  **independiente** (el OCR real que vio el modelo) aparece el costo honesto.
- **Calibración**: ¿la confianza que reporta el modelo coincide con cuánto acierta? Si dice
  "99 % seguro" y se equivoca igual, está **mal calibrado**.
- **Falso positivo (FP) / falso negativo (FN)**: FP = rechazar una factura buena; FN = dejar
  pasar una mala. El FN es el caro: un dato inventado que llega al PDF sin que nadie lo note.

> ### Epoch fencing — la pieza central (de ahí el nombre del repo)
> **En una frase:** si un proceso "zombi" revive y quiere guardar un resultado viejo, el
> sistema lo rechaza porque su número de intento (*epoch*) quedó atrasado. Así una factura
> reprocesada tras una caída nunca termina duplicada ni pisada.
>
> **En detalle:** cada intento de procesamiento de una factura lleva un número de **epoch**
> monotónico (que solo sube). El
> *store* valida ese epoch contra el estado **durable** (disco), bajo un lock de fichero
> (`flock`) que serializa a todos los procesos, y **fusiona la comprobación del token con la
> escritura en una sola operación atómica** (sin TOCTOU entre procesos). Eso es lo que
> convierte la reclamación de trabajo tras una caída en **exactly-once *effect***: un worker
> "zombi" que se daba por muerto, revive y escribe tarde **no puede pisar** el resultado
> bueno —su epoch quedó obsoleto y la escritura se rechaza—. La garantía correcta es
> *at-least-once execution* + *exactly-once effect*, **no** *exactly-once processing*.

## Qué hicimos y cómo

Partimos de dos trabajos propios —**LLM-GSR** (reconstrucción) y **VERA** (verificación)— y
construimos encima una capa de **resiliencia ante fallos** y una **auditoría experimental
honesta**:

1. **Recuperación ante caídas.** Cada factura es una tarea atómica con un *lease* que vence:
   si un nodo cae, la factura sin confirmar se reclama y se reejecuta en otro worker. El
   **epoch fencing** (arriba) hace que la reejecución no duplique ni corrompa el resultado.
2. **VERA como compuerta.** Un verificador determinista decide qué entra al PDF como
   *verificado*. Dos dimensiones ortogonales: **cobertura** (¿cada valor ancla en la fuente?
   → atrapa **alucinaciones**) y **cálculo** (¿se cumplen los invariantes aritméticos? →
   atrapa **corrupción silenciosa**).
3. **Tres modos de reconstrucción** con interfaz común: *oracle* (baseline), *conservador*
   (conserva lo legible, abstiene lo ilegible con `[UNRECOVERABLE]`) y **LLM-GSR real**
   (backend local `qwen3.5:35b` vía Ollama). Los modos productivos **no acceden a la verdad**
   (verificado por test → sin fuga de ground truth en inferencia).
4. **Fortalecimiento honesto.** Estresamos VERA con **10 familias adversariales** (importe
   inventado pero aritméticamente consistente, CUIT válido incorrecto, valor de otro
   documento, línea omitida, etc.) y la verificamos contra una **fuente independiente** (el
   OCR degradado, no un texto derivado de la verdad). Hallazgo central: el *100 % / 0 FP* del
   baseline **depende de la fuente privilegiada** —es casi circular—; con una fuente
   independiente la **cobertura deja de ser compuerta dura** (falso-rechazo alto sobre entrada
   ruidosa) y el **cálculo**, robusto, caza solo rupturas aritméticas. Medimos además calidad
   real del LLM (recupera de verdad pero alucina más al degradarse el OCR) y calibración (el
   modelo está sobreconfiado → la abstención estructural es la palanca efectiva).

Todo reproducible: semillas fijas, salidas en `data/`, **385 tests** (`385 passed, 1
skipped`) y el informe LaTeX compilado (`informe/informe.pdf`, §Fortalecimiento). Las
métricas completas están en `RESULTS.md`.

Reutiliza dos trabajos propios (en `trabajo/`):
- **LLM-GSR** (`llm_guide.pdf`): reconstrucción de OCR degradado como predicción de
  secuencia, paralelizada sobre Ray (*Pure Dispatcher*), con la **Precondición de
  Reconstrucción** (Prop. 1) y el sentinel `[UNRECOVERABLE]`.
- **VERA** (`framework/vera/`): verificación determinista de extracciones LLM.

Lo nuevo de esta materia: **recuperación ante fallos**, el **verificador como compuerta** y
la **evaluación bajo inyección de fallos**.

## Modelo de fallo (4 fallos)

| # | Fallo | Qué pasa | Cómo se cubre |
|---|-------|----------|---------------|
| 1 | **Caída de nodo** | un worker cae a mitad de una factura | **Lease con vencimiento**: la factura sin confirmar se reclama y se reejecuta en otro worker (`leases.py`) |
| 2 | **Ejecución zombi** | se dio por muerto a un worker que sigue vivo y escribe tarde | **Epoch fencing**: cada intento lleva un epoch; el store rechaza la escritura con epoch obsoleto (`store.py`) |
| 3 | **Corrupción silenciosa** | un número rompe un invariante aritmético (bien formado pero no cuadra) | VERA, dimensión **CÁLCULO** (`verify_calc`) |
| 4 | **Alucinación** | valor fabricado sin base en la fuente (el modelo no emitió `[UNRECOVERABLE]`) | VERA, dimensión **COBERTURA** (`match_coverage` → `INVENTED`) |

Los fallos 3 y 4 son los dos regímenes de la Prop. 1 del paper: cuando la información mutua
entre el OCR y el ground-truth cae por debajo del umbral, la reconstrucción colapsa a
muestrear del prior → **alucinación**; la respuesta honesta sería `[UNRECOVERABLE]`.

## Arquitectura

```
Facturas OCR → Cola con leases → Workers (Ray, temp 0, semilla fija) → Store (epoch fencing)
                     ↑ caída: se reclama                                      │ commit aceptado
                     └──────────────────────────────                         ▼
                                              DEGRADED ←── no pasa ── VERA ──pasa──→ PDF final
                                           (OCR crudo, marcado)   (compuerta)   (verificadas + marcadas)
```

Cada factura = unidad de trabajo = unidad de recuperación. El Dispatcher reparte/monitorea/
agrega; **no reconstruye** (Pure Dispatcher).

## Cómo correr

> El `.venv` del proyecto hereda ROS y contamina `PYTHONPATH`. Usá el venv limpio `.venv311`
> y neutralizá el entorno ROS:

```bash
# instalar (una vez)
python3.11 -m venv .venv311 && .venv311/bin/pip install -r requirements.txt

# prefijo recomendado para todo comando:
RUN="env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv311/bin/python"

$RUN run.py exp1            # Experimento 1 — caídas de nodo (none / reclaim / reclaim+epoch)
$RUN run.py exp2            # Experimento 2 — corrupción + alucinación (con / sin VERA)
$RUN run.py all             # ambos
$RUN run.py pdf --n 12      # genera data/facturas.pdf (verificadas + degradadas)
$RUN run.py exp2 --real     # ... con facturas REALES de Desktop/datos-demo
$RUN run.py exp1 --real     # idem para caídas de nodo
$RUN run.py ray --n 30      # corre el pipeline sobre Ray (reconstrucción paralela + recuperación)
$RUN run.py ray --n 30 --redis   # ... con el store de epoch fencing EN REDIS
$RUN run.py redis           # demo Redis: store distribuido + NDL persistente (usa el .env de mika)

# tests (VERA + pipeline)
env -u PYTHONPATH PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONNOUSERSITE=1 \
    .venv311/bin/python -m pytest framework/vera/tests tests -q
```

## Experimentos y métricas

**1 · Caídas de nodo.** Matamos workers y comparamos tres modos sobre el mismo corpus y plan
de caídas. Medimos facturas perdidas, duplicadas, tiempo de recuperación y si el resultado
coincide con una corrida sin fallos:

| modo | perdidas | duplicadas | PDF ≡ sin fallos |
|------|----------|------------|------------------|
| `none` | muchas | 0 | ✗ |
| `reclaim` | 0 | >0 (zombi pisa) | ✗ |
| `reclaim+epoch` | 0 | 0 | ✓ |

**2 · Corrupción y alucinación.** Sobre facturas reconstruidas correctamente inyectamos
errores controlados (importes, fechas, CUIT) y comparamos con y sin VERA. Como los
inyectamos nosotros, tenemos la verdad de referencia sin anotación manual. Medimos tasa de
detección, falsos positivos y falsos negativos. Resultado típico: **con VERA** detección
≈1.0, FP=0, FN=0; **sin VERA** detección 0, todas las malas entran al PDF.

> ⚠️ **Salvedad de honestidad:** ese *100 % / 0 FP* se mide contra una **fuente privilegiada**
> (texto limpio derivado de la verdad) y por tanto es casi circular. El *fortalecimiento*
> (`exp3`, fuente independiente = OCR degradado) muestra el costo real: la cobertura ya no es
> compuerta dura y solo el cálculo se mantiene robusto. Ver §"Qué hicimos y cómo" y `RESULTS.md`.

## Corpus: sintético o REAL

Por defecto el corpus es sintético (facturas es-AR generadas de forma determinista). Con
`--real` se usan **facturas reales** de `Desktop/datos-demo` (CompraGamer / Newton Station):

- `pipeline/real_corpus.py` extrae el texto de cada PDF con `pdftotext` (proxy del OCR del
  documento = fuente de verificación de VERA), parsea la extracción (comprobante, CUIT,
  fecha, total y líneas) y degrada el texto con ruido OCR sintético (simula PaddleOCR 100 DPI).
- Las líneas se adjuntan para la dimensión de CÁLCULO solo cuando Σlíneas = total (Factura B
  sin IVA discriminado), evitando falsos positivos; el resto se verifica por COBERTURA.
- Las facturas reales reconstruidas correctamente pasan VERA sin falsos positivos
  (`tests/test_real_corpus.py` lo verifica). ~50 facturas, ~20 con cálculo.

Requiere `poppler-utils` (`pdftotext`). Default: `/home/athenas/Desktop/datos-demo`
(cambiable con `--demo-dir`).

## Backend Redis (infra de mika)

Lee la config del `.env` de mika (`MIKA_ENV`, o `gen-ai-backend/.env`) — tratado como
privado, nunca se imprime ni se envía afuera. Dos usos reales de Redis:

- **Store con epoch fencing en Redis** (`redis_store.py`): el rechazo de escrituras zombi se
  hace con un script Lua atómico (compare-and-set sobre el epoch) → la recuperación es real
  entre procesos/nodos, no simulada. Drop-in del store en memoria (`--redis`).
- **NDL de VERA en Redis** (`RedisNDLStore`): memoria diacrónica de anti-patrones. El gate
  registra cada alucinación detectada; en corridas futuras VERA ya reconoce el patrón.

Todo con **degradación elegante**: si Redis no está alcanzable, el pipeline cae a los
backends en memoria. Las claves usan el namespace `tf:` (no colisionan con las de mika).

## Estructura

```
framework/vera/        VERA (verificador determinista, port de producción)
config/default/        vera_contracts.yml (contrato del doc_type 'factura')
pipeline/
  corpus.py            genera facturas (truth + OCR degradado + texto reconstruido)
  reconstructor.py     3 modos: oracle, conservador y LLM-GSR real (interfaz común, sentinel)
  domain_context.py    contexto de dominio φ (esquema del doc_type desde el contrato VERA)
  llm_client.py        cliente LLM (OpenAI gpt-4o / Ollama local; key solo en runtime)
  abstention.py        política de abstención (confianza + evidencia); encuadre de I(ĉ;s)
  adversarial.py       10 familias de fallos adversariales para estresar a VERA
  leases.py            cola con leases → recuperación ante caída de nodo
  store.py             store atómico con epoch fencing durable (flock, sin TOCTOU)
  gate.py              compuerta VERA (cobertura → alucinación; cálculo → corrupción)
  faults.py            inyección: caída de nodo, corrupción, alucinación
  orchestrator.py      ejecutor local determinista (motor de métricas)
  ray_dispatcher.py    ejecutor Ray (Pure Dispatcher, paralelo)
  real_corpus.py       carga facturas REALES de datos-demo (pdftotext + parser)
  mika_env.py          carga el .env de mika (Redis, flags VERA) — privado
  redis_store.py       store con epoch fencing EN REDIS (CAS atómico vía Lua)
  pdf.py               PDF final (verificadas + degradadas marcadas)
experiments/           exp1_node_crash · exp2_content · exp2_real (calidad LLM-GSR)
                       exp3_vera_adversarial (10 familias) · exp4_abstention (τ/calibración)
run.py                 CLI
```

## Limitaciones

Corpus de un solo dominio (facturas). Ejecución en un nodo con caídas simuladas (reloj
virtual determinista en el ejecutor local; Ray para el paralelismo real), sin red real. La
corrupción inyectada no reproduce todos los errores de un LLM, y VERA solo detecta lo que
sus reglas cubren.
