# Mediciones para el informe — Fallos y Resiliencia

> **Nota (fortalecimiento, auditoría 2026-11-13).** Este documento tiene dos partes:
> **(A)** el baseline original (Exp. 1 y 2), que usa un reconstructor *oracle* y verifica VERA
> contra una fuente **privilegiada** (`clean_text`) — útil como techo pero con dos salvedades
> fuertes (reconstrucción = verdad; verificación casi-circular); y **(B)** el
> *fortalecimiento honesto* (§Fortalecimiento), con reconstructor real (LLM-GSR), fuente de
> verificación **independiente**, fallos adversariales, fencing atómico entre procesos y
> criterio de abstención. Donde (A) exagera, (B) lo acota con números medidos.

Resultados de los experimentos del pipeline distribuido de reconstrucción de facturas con
compuerta VERA. Datos crudos en `data/**/*.json` y `data/measure/*.csv`.

## Montaje experimental

- **Corpus**: sintético (100 facturas es-AR generadas de forma determinista) y **real**
  (~50 facturas de CompraGamer / Newton Station S.R.L. de `Desktop/datos-demo`, texto
  extraído con `pdftotext`, degradado con ruido OCR sintético que simula PaddleOCR 100 DPI).
- **Reconstructor**: determinista (`temperatura 0`, semilla por `(invoice_id, epoch)`) →
  reejecución repetible. Sobre la reconstrucción correcta se inyectan los fallos.
- **Plano de cómputo**: Ray (patrón *Pure Dispatcher*); las métricas de recuperación se
  obtienen con el ejecutor local determinista (reloj virtual), que modela caídas y zombis
  de forma reproducible. Store con epoch fencing en disco y, opcionalmente, en Redis.
- **Semillas**: Exp. 1 → 8 semillas × 5 fracciones de caída; Exp. 2 → 10 semillas × 2 corpus.

---

## Experimento 1 — Recuperación ante caídas de nodo

Se matan workers durante la corrida (fracción variable) y se comparan tres modos, cada uno
activando un mecanismo. Corpus de 50 facturas. Figura: `data/figuras/exp1_recuperacion.png`.

**Tabla 1 — Resultado por modo (fracción de caídas = 30 %, media de 8 semillas; desvío = 0).**

| Modo | Facturas perdidas | Facturas duplicadas | Reclamaciones | Zombis rechazados | T. recuperación | PDF ≡ sin fallos |
|------|:---:|:---:|:---:|:---:|:---:|:---:|
| `none` (sin recuperación) | **15** | 0 | 0 | 0 | — | ✗ |
| `reclaim` (lease) | 0 | **15** | 15 | 0 | 2.0 | ✗ |
| `reclaim+epoch` (lease + fencing) | 0 | **0** | 15 | **15** | 2.0 | ✓ |

**Tabla 2 — Escalado con la fracción de caídas (facturas perdidas / duplicadas, media; n=50).**

| Fracción caídas | `none` perdidas | `reclaim` duplicadas | `reclaim+epoch` perdidas/dup |
|:---:|:---:|:---:|:---:|
| 10 % | 5 | 5 | 0 / 0 |
| 20 % | 10 | 10 | 0 / 0 |
| 30 % | 15 | 15 | 0 / 0 |
| 40 % | 20 | 20 | 0 / 0 |
| 50 % | 25 | 25 | 0 / 0 |

**Hallazgos.**
1. **Sin recuperación**, las facturas perdidas crecen linealmente con la fracción de caídas
   (toda factura cuyo worker cae se pierde): de 5 a 25 facturas (10 %→50 %).
2. **Solo con reclamación** no se pierde ninguna factura, pero cada worker zombi que
   resucita pisa el record bueno → tantas facturas **duplicadas/corruptas** como caídas.
3. **Con reclamación + epoch fencing** no hay pérdidas ni duplicados: los 15 zombis son
   rechazados por el store (epoch obsoleto), y el resultado final **coincide bit a bit con
   la corrida sin fallos** (único modo con `PDF ≡ sin fallos`).
4. El desvío entre semillas es **0**: la recuperación es determinista por diseño (semilla
   fija + escritura atómica), no depende de qué workers caen. Tiempo de recuperación
   constante = 1 vencimiento de lease.

---

## Experimento 2 — Corrupción silenciosa y alucinación (compuerta VERA)

Sobre facturas reconstruidas correctamente se inyectan errores controlados (25 % corrupción,
25 % alucinación); se compara **con** y **sin** VERA. Verdad de referencia por construcción.
Figuras: `data/figuras/exp2_deteccion.png`, `data/figuras/exp2_falsos_negativos.png`.

**Tabla 3 — Detección por corpus y condición (media de 10 semillas; desvío = 0).**

| Corpus | Condición | Tasa de detección | Falsos positivos | Falsos negativos | Detección corrupción | Detección alucinación |
|--------|-----------|:---:|:---:|:---:|:---:|:---:|
| sintético (100) | **con VERA** | **1.00** | 0 | 0 | 1.00 | 1.00 |
| sintético (100) | sin VERA | 0.00 | 0 | **50** | 0.00 | 0.00 |
| real (~50) | **con VERA** | **1.00** | 0 | 0 | 1.00 | 1.00 |
| real (~50) | sin VERA | 0.00 | 0 | **24** | 0.00 | 0.00 |

**Hallazgos.**
1. **Con VERA**: detección perfecta (100 %) de ambos fallos, con **cero falsos positivos** y
   **cero falsos negativos**, tanto en el corpus sintético como en el de facturas **reales**.
2. **Sin VERA**: ninguna factura corrupta se detecta → **todas** entran al PDF (50 en el
   sintético, 24 en el real). Es el costo de verificar "exactamente una vez" sin verificar
   el *contenido*.
3. Cada fallo lo caza una **dimensión distinta** de VERA: la corrupción silenciosa por la
   dimensión de **cálculo** (rompe un invariante aritmético), la alucinación por la de
   **cobertura** (valor sin anclaje en la fuente). Las dos son necesarias.
4. ⚠️ **Salvedad (corregida en la parte B).** Estos 100 %/0 FP se obtienen verificando contra
   `clean_text` (texto limpio derivado de la verdad / `pdftotext` de la capa limpia), que es una
   **referencia privilegiada**: VERA termina comparando la reconstrucción contra algo derivado de
   la verdad. No debe leerse como garantía universal. Con una fuente **independiente** (el OCR
   degradado) y gate estricto, los números cambian radicalmente (ver §Fortalecimiento, Exp. 3).

---

## Síntesis para el informe

| Problema | Mecanismo | Resultado medido |
|----------|-----------|------------------|
| Pérdida de trabajo por caídas | lease + reclamación | 0 facturas perdidas (vs. 15/50 sin recuperación a 30 %) |
| Escritura zombi tardía | epoch fencing | 0 duplicados (vs. 15/50 solo con reclamación); PDF ≡ sin fallos |
| Corrupción silenciosa | VERA · dimensión cálculo | 100 % detección, 0 FN (vs. todas sin VERA) |
| Alucinación | VERA · dimensión cobertura | 100 % detección, 0 FP, 0 FN |

**La idea central queda cuantificada**: procesar cada factura *exactamente una vez*
(lease + epoch fencing → 0 perdidas, 0 duplicadas) **no** garantiza un resultado correcto;
sin la compuerta VERA, 24–50 facturas con datos corruptos o inventados entran al PDF sin que
el sistema lo note.

---

# Fortalecimiento (auditoría 2026-11-13) — resultados honestos

Corrige las dos salvedades de la parte A: el reconstructor ahora es **real** y la verificación
usa una fuente **independiente**. Tres modos con interfaz común (los productivos NO acceden a la
verdad; probado en `tests/test_reconstructors.py`):

- **A — Oracle** (`OracleReconstructor`): baseline reproducible, tiene la verdad → techo, NO es inferencia.
- **B — LLM-GSR** (`LLMReconstructor`): ve solo OCR + esquema de dominio. Backend: OpenAI `gpt-4o`
  o, al bloquearse por límite de gasto, **Ollama local `qwen3.5:35b`** (gratis).
- **C — Conservador** (`ConservativeReconstructor`): conserva lo legible, abstiene lo ilegible.

## Exp. 2-real — Calidad de reconstrucción (Oracle vs Conservador vs LLM)

`experiments/exp2_real.py`, n=8/grado, LLM=`qwen3.5:35b`. Gold comparado **solo en el evaluador**.

| grado | modo | exactitud campo | exact. si responde | factura exacta | alucinación | abstención | error num. | latencia |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| clean | conservador | 0.911 | 0.981 | 0.50 | 0.018 | 0.071 | 0.042 | <1 ms |
| clean | **llm** | 0.982 | 0.982 | 0.875 | 0.018 | 0.00 | 0.042 | 24 s |
| degraded | conservador | 0.696 | 0.951 | 0.125 | 0.036 | 0.268 | 0.083 | <1 ms |
| degraded | **llm** | 0.893 | 0.909 | 0.375 | 0.089 | 0.018 | 0.125 | 36 s |
| poor | conservador | 0.554 | 0.838 | 0.00 | 0.107 | 0.339 | 0.125 | <1 ms |
| poor | **llm** | 0.786 | 0.830 | 0.125 | 0.161 | 0.054 | 0.083 | 36 s |

*(Oracle = 1.0 en todo por construcción. Tokens LLM: 128 k salida / 18 k entrada en 24 llamadas.)*

**Hallazgos.** (1) El LLM recupera de verdad: exactitud 0.98/0.89/0.79 > conservador 0.91/0.70/0.55.
(2) **Trade-off abstención↔alucinación (Prop. 1 del paper)**: el conservador abstiene cada vez más
al degradarse el OCR (0.07→0.27→0.34) y alucina poco; el LLM abstiene poco (0→0.02→0.05) pero
**alucina más** (0.018→0.089→**0.161**) — cuando baja la información recuperable, muestrea del prior.
(3) El LLM comete **errores numéricos** crecientes (hasta 0.125): importes plausibles que *pasan* el
chequeo de cálculo.

## Exp. 3 — Capacidad real de VERA (10 fallos adversariales)

`experiments/exp3_vera_adversarial.py`, 120 casos (81 con error, 39 limpias), gate estricto.
Clase positiva = factura con error. IC95 de Wilson.

| fuente | config | recall | FRR | precision | F1 |
|---|---|:--:|:--:|:--:|:--:|
| **privilegiada** (`clean_text`) | cobertura+cálculo | 1.00 | 0.00 | 1.00 | 1.00 |
| **privilegiada** | solo cálculo | 0.432 | 0.00 | 1.00 | 0.603 |
| **independiente** (OCR degradado) | cobertura+cálculo | 1.00 | **0.897** | 0.698 | 0.822 |
| **independiente** | solo cálculo | 0.432 | **0.00** | 1.00 | 0.603 |

**Hallazgos.** (1) El "100 %/0 FP" de la parte A **solo existe con la fuente privilegiada** — es la
circularidad que denunciaba la auditoría. (2) Con fuente independiente, la **cobertura no es
compuerta dura** (FRR 0.90): el ruido OCR impide anclar incluso valores correctos → sirve como
señal de *revisar*, no como prueba. (3) El **cálculo es robusto e independiente de la fuente**
(FRR 0, precision 1.0) pero **no generaliza**: recall 0.43 (solo rupturas aritméticas; los fallos
consistentes/escalados se le escapan). Las 10 familias se detectan con la fuente independiente,
pero a costa del FRR.

## Fencing atómico entre procesos (sección 6)

El commit del store valida el epoch contra el estado **durable** (disco), no la caché, dentro de un
lock de fichero (`flock`) que serializa a todos los procesos → comprobación-del-token + escritura
en **una sola operación atómica** (sin TOCTOU entre procesos). Probado con dos instancias y con
**dos procesos reales** (`tests/test_fencing.py::test_fencing_multiproceso_real`).

**Denominación correcta de la garantía:** *at-least-once execution* (un intento caído se re-ejecuta)
+ **exactly-once effect** (commits idempotentes con epoch fencing → un record por factura, el de
mayor epoch; zombis rechazados). **No** es *exactly-once processing*.

## Abstención: abstener vs "reconstruir siempre" (sección 4)

`experiments/exp4_abstention.py`. Sobre I(ĉ;s): es poblacional y **no** se mide por documento (no
hay estimador insesgado de un solo par); se usan señales observables — **evidencia** (anclaje en la
fuente) e **incertidumbre predictiva** (1−confianza del modelo) — y la recuperabilidad se mide
empíricamente a nivel corpus (Exp. 2-real). Barrido del umbral τ (τ=0 = reconstruir siempre):

LLM=`qwen3.5:35b`, OCR degradado, n=16 (`data/exp4/exp4_llm_degraded.json`):

| τ (umbral confianza) | cobertura | exactitud | riesgo (error entre respondidos) |
|:--:|:--:|:--:|:--:|
| 0.0 (reconstruir siempre) | 0.991 | 0.884 | 0.108 |
| 0.3 | 0.991 | 0.884 | 0.108 |
| 0.5 | 0.991 | 0.884 | 0.108 |
| 0.7 | 0.982 | 0.884 | 0.100 |
| 0.9 | 0.964 | 0.884 | 0.083 |

**Hallazgo (sobreconfianza / mala calibración).** Subir el umbral **casi no reduce el riesgo**
(0.108→0.083) y apenas recorta cobertura: qwen3.5:35b reporta confianza **uniformemente alta** y
da evidencia incluso cuando se equivoca → su confianza **no separa aciertos de errores**. La
abstención *por confianza* es, por tanto, un lever débil con este modelo; la abstención
**estructural por evidencia** del modo conservador (Exp. 2-real: abstención 0.27 a `degraded`, con
menos alucinación) es la palanca honesta efectiva. Conclusión medida: haría falta una **confianza
calibrada** para que la abstención por umbral funcione; la autoconfianza cruda del LLM no lo está.

## Salvedades que siguen abiertas (honestas)

- Las caídas de nodo del ejecutor local son **simuladas** (reloj virtual, `InjectedCrash`); Ray
  corre reconstrucción real en paralelo pero las caídas también son inyectadas. Falta el kill de
  proceso real / partición de red (S7, pendiente).
- Exp. 2-real y 4 con n chico (coste del LLM local); los IC son amplios.
- El contrato `factura` por defecto no cubre impuestos internos / multi-alícuota de algunas
  facturas reales transcritas.

## Reproducir

```bash
RUN="env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv311/bin/python"
$RUN -m experiments.exp1_node_crash          # recuperación (parte A)
$RUN -m experiments.exp2_real                # calidad reconstrucción (B) — usa LLM
$RUN -m experiments.exp3_vera_adversarial    # VERA adversarial (B)
$RUN -m experiments.exp4_abstention          # abstención (B)
$RUN -m experiments.measure && $RUN -m experiments.figures   # barridos + figuras (A)
```
