# PENDING EXPERIMENT — SelfCheckGPT / RAGTruth / FActScore (diferidos, con justificación)

El comentario `% === PENDING EXPERIMENT: adapted-sota-baselines ===` en `tab:baselines`
(`docs/paper/mika_ipm_R1_marked.tex`) pedía una comparación side-by-side de VERA vs. **SelfCheckGPT,
RAGTruth y FActScore adaptados**. Este documento registra por qué esos tres se **difieren** y por qué
la cobertura actual (HHEM + MiniCheck + LLM-judge NLI) es suficiente como comparación SOTA.

**Estado del bloque en el `.tex`:** anotado como *PARTIALLY ADDRESSED* — HHEM-2.1-Open y MiniCheck
(pure-classifier) reportados en `tab:baselines-sota`; estos tres pendientes si un revisor los exige.

## Por qué la cobertura actual es representativa

La clase "post-hoc hallucination detector" tiene tres familias; cubrimos las tres o mostramos que
no aplican:

| Familia | Representante en la tabla | Los tres pedidos |
|---|---|---|
| Pure-classifier de entailment (NLI fine-tuneado) | **HHEM-2.1-Open, MiniCheck** (2 filas × 2 scopes) | — |
| LLM-judge de entailment (yes/no contra fuente) | **LLM-judge (NLI)** | RAGTruth, FActScore colapsan acá (ver abajo) |
| Sampling-consistency (k muestras del LLM) | SelfCheckGPT | **no aplica a inyección** (ver abajo) |

### SelfCheckGPT — NO APLICA a la injection benchmark (por diseño)

SelfCheckGPT detecta alucinación midiendo la (in)consistencia entre **muestras re-generadas por el
MISMO LLM** (temp>0). Requiere que el valor evaluado sea una **salida del LLM**. En la injection
benchmark el valor NO es una salida del LLM: se **inyecta** determinísticamente desde la verdad
(digit_sub / magnitude / migration). No hay distribución de muestras del LLM para ese valor → el
método es inaplicable, no "malo". Ya está anotado así en `stress_baselines.py`
(`SelfCheckGPT = NO APLICA`). Está **implementado** (`baselines.py::selfcheck_agreement`) y corre en
el setting natural (extracción real), donde sí hay muestras — no en el de inyección.

### RAGTruth — su verificación ES entailment NLI (ya representada)

RAGTruth es (i) un **dataset** de respuestas RAG etiquetadas y (ii) detectores **entrenados/adaptados**
para juzgar si un span está respaldado por el contexto. La operación de verificación es **entailment
claim↔fuente**, exactamente lo que hace la fila **LLM-judge (NLI)** (y, sin LLM, HHEM/MiniCheck). El
propio `.tex` describe el baseline NLI como *"an NLI/RAGTruth-style adaptation that asks whether the
value is supported"*. Correr el detector RAGTruth específico agregaría un checkpoint más de la MISMA
familia, no una capacidad nueva.

### FActScore — para un campo de precisión colapsa a NLI atómico (ya representado)

FActScore descompone un texto en **hechos atómicos** y verifica cada uno contra una fuente
(retrieval + NLI). Un campo de precisión ("The Total is X") **ya es un único hecho atómico**: la
descomposición es la identidad y FActScore se reduce a **verificar ese hecho contra la fuente** = la
fila LLM-judge (NLI). Además FActScore asume un knowledge source con retrieval; acá la fuente es el
documento OCR completo (scope `ctx`) o la región (scope `region`), que es justo lo que barremos.

## Qué haría falta para correrlos igual (si un revisor lo pide)

| Detector | Requiere | Costo/riesgo | Aporte esperado |
|---|---|---|---|
| SelfCheckGPT | Correr en setting **natural** (no inyección); k muestras temp>0 por campo | k+ llamadas OpenAI/campo | Mide consistencia, no fidelidad a la fuente; ya en `baselines.py` |
| RAGTruth detector | Checkpoint RAGTruth (HF) + adaptador de I/O | 1 modelo más, misma familia NLI | Redundante con LLM-judge/HHEM |
| FActScore | Pipeline atómico + retriever + verificador | Pesado; degenera a NLI en 1-hecho | Redundante con LLM-judge |

## Conclusión

La comparación SOTA de `tab:baselines-sota` cubre la clase pure-classifier con **dos** representantes
(HHEM, MiniCheck) en ambos scopes, más el LLM-judge (NLI) que subsume la verificación tipo
RAGTruth/FActScore, y explicita que SelfCheckGPT no aplica al protocolo de inyección. Los tres
detectores nominales quedan **diferidos** por redundancia/ inaplicabilidad, no por omisión.
