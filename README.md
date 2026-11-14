# Fallos y Resiliencia — pipeline distribuido de reconstrucción de facturas + VERA

Pipeline distribuido (Ray) que reconstruye facturas degradadas por OCR y **sobrevive a la
caída de nodos sin perder ni duplicar**. Al final, un verificador determinista (**VERA**)
actúa como **compuerta**: solo lo que avala entra al PDF como *verificado*; lo demás entra
con su OCR crudo y **marcado** (`DEGRADED`).

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
  reconstructor.py     LLM-GSR determinista (temp 0, semilla por (id, epoch)), sentinel
  leases.py            cola con leases → recuperación ante caída de nodo
  store.py             store atómico con epoch fencing → rechazo de zombis
  gate.py              compuerta VERA (cobertura → alucinación; cálculo → corrupción)
  faults.py            inyección: caída de nodo, corrupción, alucinación
  orchestrator.py      ejecutor local determinista (motor de métricas)
  ray_dispatcher.py    ejecutor Ray (Pure Dispatcher, paralelo)
  real_corpus.py       carga facturas REALES de datos-demo (pdftotext + parser)
  mika_env.py          carga el .env de mika (Redis, flags VERA) — privado
  redis_store.py       store con epoch fencing EN REDIS (CAS atómico vía Lua)
  pdf.py               PDF final (verificadas + degradadas marcadas)
experiments/           exp1_node_crash.py · exp2_content.py
run.py                 CLI
```

## Limitaciones

Corpus de un solo dominio (facturas). Ejecución en un nodo con caídas simuladas (reloj
virtual determinista en el ejecutor local; Ray para el paralelismo real), sin red real. La
corrupción inyectada no reproduce todos los errores de un LLM, y VERA solo detecta lo que
sus reglas cubren.
