# RIGOR EXPERIMENTAL — extensiones sobre la comparación SOTA

Experimentos de rigor **más allá** de la tabla principal (`tab:baselines-sota`, n=500 / 10.000
samples, injection). Dos corridos, dos documentados como no-corridos con su razón. La tabla
principal del paper **no cambia**; esto la complementa.

| Experimento | Estado | Aporte |
|---|---|---|
| A. NLI en scope `region` | ✅ corrido | completa la grilla detector×scope |
| B. Modo `natural` (extracciones reales del LLM) | ✅ corrido | FP/especificidad sobre datos reales |
| C. Bespoke-MiniCheck-7B | ⛔ no-corrido (riesgo de entorno) | pure-classifier más fuerte |
| D. Región con bboxes reales (RD553) | ⛔ infeasible (corpus ausente) | región real vs proxy |

---

## A · LLM-judge (NLI) en scope `region`

En la tabla principal el NLI corre solo en `ctx` (region iba detrás de `--judge-region`, off por
costo). Acá se corrió sobre los MISMOS 10.000 samples (2.876 premises de región únicos; scorer
paralelizado, 8 workers, timeout 60s).

| Detector | scope | single | oom | migr | wrong | FP (correct) | IC FP 95% |
|---|---|---|---|---|---|---|---|
| LLM-judge (NLI) | ctx | 1.000 | 1.000 | 1.000 | 1.000 | 0.021 | [0.017, 0.026] |
| **LLM-judge (NLI)** | **region** | 0.989 | 1.000 | 1.000 | 0.996 | **0.048** | [0.042, 0.056] |

**Hallazgo.** Incluso el LLM-judge —el baseline más fuerte— **paga FP al recortar a la región**:
su FP se **duplica** (0.021 → 0.048) sin ganar captura. Aun así queda muy por debajo de los
pure-classifiers en region (HHEM 0.113, MiniCheck 0.406). Ningún scope del NLI alcanza la esquina de
VERA (1.00 captura / **0.00** FP): ctx paga 0.021, region 0.048. (El NLI `ctx` reproduce la tabla
principal: FP 0.021 vs 0.024 — variación esperada del LLM a temp 0.)

Artefactos: `results/vera_rigor_nli_region/`.

## B · Modo `natural` — FP sobre extracciones REALES del LLM

Extracción real (gpt-5.4-mini, temp 0) sobre 150 facturas → **1.050 campos: 989 `correct`,
61 `other_wrong`** (discrepancias naturales; el LLM casi no alucina en texto limpio — el mismo
caveat que reporta el paper). Mide especificidad (FP) sobre extracciones reales, no inyectadas.

| Detector | scope | FP (989 correct reales) | IC 95% |
|---|---|---|---|
| **VERA** | region | **0.000** | [0.000, 0.004] |
| Lexical | ctx | 0.000 | [0.000, 0.004] |
| LLM-judge (NLI) | ctx | 0.002 | [0.001, 0.007] |
| HHEM-2.1-Open | ctx | 0.001 | [0.000, 0.006] |
| HHEM-2.1-Open | region | 0.104 | [0.087, 0.125] |
| MiniCheck | ctx | 0.010 | [0.006, 0.019] |
| MiniCheck | region | 0.233 | [0.207, 0.260] |

**Hallazgo.** La especificidad se sostiene en datos reales: **VERA 0 FP sobre 989 extracciones
reales**; los pure-classifiers en scope `region` falsean fuerte (HHEM 0.10, MiniCheck 0.23), igual
que en inyección. (La captura sobre los 61 `other_wrong` es secundaria: N chico y etiquetas
ambiguas; el valor del experimento es el FP.)

Artefactos: `results/vera_rigor_natural/`.

---

## C · Bespoke-MiniCheck-7B — NO corrido (riesgo de entorno)

MiniCheck acelera `Bespoke-MiniCheck-7B` **con vLLM**, que no está instalado. Instalar vLLM
**arrisga el entorno torch-pinned** (fija versiones de torch/CUDA; ya se acordó *no upgradear torch*,
y el bypass de `torch.load` para flan-t5-large depende de torch<2.6). El costo/beneficio no lo
justifica: **flan-t5-large es el representante pure-classifier acordado** y ya muestra el trade-off
ctx↔region. Para correrlo: `pip install vllm` (en un entorno aislado) y
`--minicheck-model Bespoke-MiniCheck-7B`; GPU de 24 GB alcanza en fp16.

## D · Región con bboxes reales (Azure DI / RD553) — INFEASIBLE aquí

El scope `region` usa hoy un **proxy de texto** (línea etiqueta+valor). La versión fuerte usaría las
**regiones geométricas reales** (bboxes de Azure DI) sobre los **104 docs OCR reales RD553** que el
paper ya cita. Ese corpus está **gitignoreado (PII real: NIF/CIF, actas)** y **no está presente** en
este entorno → no se puede correr acá. Requiere el corpus real + credenciales de Azure DI; es la vía
para cerrar el caveat "region_id/region_truth = proxy".

---

## Nota de código — hardening de `nli_scorer`

El barrido NLI region (2.876 llamadas) con el scorer **secuencial y sin timeout** original era
frágil (un request lento frena todo). Se endureció: cliente OpenAI con `timeout=60`/`max_retries=2`
y **paralelismo** (`ThreadPoolExecutor`, 8 workers), con fallback a *no-supported* si un grupo falla
tras reintentos. Los tests offline (fake scorer) siguen verdes.

## Reproducir

```bash
# A) NLI region (reusa los samples del run principal):
python -m framework.vera.eval.baselines_sota --samples results/vera_baselines_sota/samples.jsonl \
    --no-vera --no-lexical --no-hhem --no-minicheck --judge-region --out results/vera_rigor_nli_region
# B) modo natural (extracción real; usa OpenAI):
python -m framework.vera.eval.baselines_sota --mode natural --n 150 --seed 20260703 \
    --out results/vera_rigor_natural
```

Ver también `PENDIEND_EXP.md` (SelfCheckGPT / RAGTruth / FActScore: diferidos con justificación).
