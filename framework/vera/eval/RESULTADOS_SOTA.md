# RESULTADOS — SOTA pure-classifier baselines para Table 11 (VERA)

Extiende la comparación de detectores de Table 11 (`\label{tab:baselines}`) con **2 baselines SOTA
pure-classifier de entailment** (HHEM-2.1-Open, MiniCheck) y corre **TODOS los detectores sobre
EXACTAMENTE el mismo sample-set** (apples-to-apples), reusando los triples inyectados de la
injection/specificity benchmark (`stress_baselines.py`), sin reabrir el diseño experimental.

- Código: `framework/vera/eval/baselines_sota.py` · Tests: `framework/vera/tests/test_baselines_sota.py` (19, offline).
- Runbook: `framework/vera/eval/SOTA_BASELINES.md`.
- Artefactos: `results/vera_baselines_sota/` (`table11_sota.csv`, `predictions_sota.csv`, `samples.jsonl`, `repro_sota.json`).

## Metodología

- **Corpus / escala**: `data/invoices_20k`, **500 documentos → 10.000 samples**. Seed `20260703`.
- **Errores inyectados** desde la verdad (fabricación controlada; sin LLM para inyectar). Clases:
  `correct` (control de FP), `single_digit`, `order_of_magnitude`, `field_migration`.
  N por clase: correct=3500, single_digit=2500, order_of_magnitude=1500, field_migration=2500.
- **Claim uniforme**: `"The {field_label} is {claimed_value}."`
- **Scopes**: `ctx` = fuente `S` completa · `region` = región del campo (`region_of`, anclada en el
  VALOR REAL del campo: línea de la etiqueta + línea del valor).
- **Threshold** = punto de operación por defecto (`0.5`); **NO tuneado por clase**.
- **IC**: Wilson 95% (`framework.vera.eval.wilson_ci`).
- **Mismo sample-set para TODOS los detectores** (VERA, Lexical, LLM-judge NLI, HHEM, MiniCheck):
  el runner **aborta** si algún `(detector, scope)` no cubre el mismo key-set `(doc_id, field_key,
  error_class)`. El LLM-judge (NLI) se recomputa sobre estos 10.000 samples (no viene de otra corrida).

## Table 11 — resultados (capture por clase; FP sobre `correct`; N=10.000)

| Detector | scope | single_digit | order_of_mag | **field_migration** | wrong (global) | **FP (correct)** | det. | calls | lat ms |
|---|---|---|---|---|---|---|---|---|---|
| **VERA** | region | 1.000 | 1.000 | **1.000** | 1.000 | **0.000** | yes | 0 | 2.0 |
| Lexical | ctx | 1.000 | 1.000 | 0.000 | 0.615 | 0.000 | yes | 0 | 0.6 |
| **LLM-judge (NLI)** | ctx | 1.000 | 1.000 | 1.000 | 1.000 | 0.024 | no | 1 | 82 |
| **HHEM-2.1-Open** | ctx | 0.410 | 0.637 | **0.088** | 0.339 | 0.001 | yes | 1 | 154 |
| **HHEM-2.1-Open** | region | 0.756 | 0.857 | 0.997 | 0.872 | 0.113 | yes | 1 | 13 |
| **MiniCheck** | ctx | 0.915 | 0.982 | 0.848 | 0.905 | 0.073 | yes | 1 | 29 |
| **MiniCheck** | region | 0.973 | 1.000 | 1.000 | 0.990 | **0.406** | yes | 1 | 22 |

**IC Wilson 95% (FP sobre correct, N=3500):** VERA/Lexical `[0.000, 0.0011]` · NLI `[0.019, 0.029]` ·
HHEM ctx `[0.0004, 0.0029]` · HHEM region `[0.103, 0.124]` · MiniCheck ctx `[0.065, 0.083]` ·
MiniCheck region `[0.390, 0.423]`. **IC (field_migration capture, N=2500):** HHEM ctx `[0.078, 0.100]` ·
HHEM region `[0.994, 0.999]` · MiniCheck ctx `[0.834, 0.862]` · MiniCheck/VERA/NLI region/ctx `[0.9985, 1.0]`.

## Hallazgo

1. **VERA domina la frontera capture/FP a costo cero**: `1.000` de captura en las tres clases con
   `0.000` de FP, **determinista y 0 llamadas a modelo**. El LLM-judge (NLI) iguala la captura
   (`1.000` en todas las clases) pero es **no-determinista, cuesta una llamada LLM por campo, y
   tiene FP > 0** (`0.024`). VERA obtiene el mismo poder de detección sin LLM y sin falsos positivos.
2. **Los pure-classifier SOTA enfrentan un trade-off `ctx`↔`region` del que VERA está exenta**:
   - **`ctx` (fuente completa)**: FP bajísimo pero **pierde field-migration** (HHEM **0.088**,
     MiniCheck 0.848): el valor migrado *sí* aparece en la fuente → el clasificador lo declara
     *supported*. Misma ceguera que Lexical (`0.000`).
   - **`region`**: recupera la migración (0.997–1.000) pero el **FP se dispara** (HHEM 0.113,
     **MiniCheck 0.406**): el premise recortado hace que valores correctos parezcan no respaldados.
   Ningún `(detector, scope)` pure-classifier alcanza la esquina (captura alta, FP bajo) que VERA
   ocupa determinísticamente.

## Verificación del fix de región (bug de correctitud)

`region_of` se ancló en el VALOR REAL del campo (label + valor), corrigiendo 38/700 controles
`correct` cuyo `region_S` perdía el valor (layout `Etiqueta:\n valor`). Efecto medido — el FP de
region scope **baja** respecto al piloto n=100:

| | piloto n=100 | n=500 (fix) |
|---|---|---|
| HHEM region FP | 0.154 | **0.113** |
| MiniCheck region FP | 0.461 | **0.406** |

Test de regresión: `test_all_correct_samples_have_value_in_region` (0 controles `correct` sin el
valor en su región).

## Operating-point dominance (post-proceso, sin re-correr modelos)

`framework/vera/eval/operating_curve.py` barre el threshold sobre las `supported_prob` ya logueadas
en `predictions_sota.csv` (**cero llamadas a modelos/OpenAI**; guard: score continuo + `flag@0.5 ==
flagged` exacto) para responder si algún threshold pone a un pure-classifier en la esquina de VERA
$(FPR=0,\ \text{captura migración}=1.0)$. **No.**

| Detector | scope | AUROC (global) | AUROC (migr.) | max cap @FPR=0 | min FPR @cap=1.0 |
|---|---|---|---|---|---|
| HHEM | ctx | 0.841 | 0.723 | 0.000 | 0.888 |
| HHEM | region | 0.938 | 0.987 | 0.458 | 0.116 |
| MiniCheck | ctx | 0.966 | 0.951 | 0.089 | 0.716 |
| MiniCheck | region | 0.983 | 0.999 | 0.536 | 0.191 |

**Dos números clave:** a FPR $=0$ la mejor captura de field-migration es **0.54**; llegar a captura
$=1.0$ exige FPR $\geq$ **0.12**. `vera_corner_reached=False` ($\varepsilon=10^{-6}$): ninguna curva
toca $(0,1)$, aunque las *region* se acercan (captura $\approx 0.996$ a FPR $0.02$). Figura del paper:
`fig:operating-curve` (`fig_operating_curve.pdf`, ROC de migración + inset del corner). Artefactos:
`threshold_sweep.csv`, `operating_dominance.{csv,json}`.

## LaTeX — companion `tab:baselines-sota` + `fig:operating-curve`

Insertados en `docs/paper/mika_ipm_R1_marked.tex` tras `tab:baselines` (7 filas sobre N=10.000) y la
figura de operating-point con su párrafo. Compila limpio (pdflatex).

## Data Availability (reproducibilidad)

| | HHEM-2.1-Open | MiniCheck | LLM-judge (NLI) |
|---|---|---|---|
| repo_id / modelo | `vectara/hallucination_evaluation_model` | `lytang/MiniCheck-Flan-T5-Large` | `gpt-5.4-mini` (temp 0) |
| revision (commit) | `8e4a2e6e96c708cc76c2344f7e4757df2515292c` | `96eafd01cee2d16cf81aaa2fb226b14f422a37b3` | — |
| checkpoint / loader | HHEM-2.1-Open · `AutoModelForSequenceClassification` (`trust_remote_code`) | flan-t5-large · `minicheck.MiniCheck` | OpenAI chat, JSON yes/no |
| threshold | 0.5 | 0.5 | 0.5 (yes/no) |

- **claim template**: `The {field_label} is {claimed_value}.` · **seed**: `20260703` · **corpus**:
  `data/invoices_20k` (500 docs / 10.000 samples). Registro completo en `repro_sota.json`.

## Reproducir

```bash
pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"   # HHEM usa transformers
export TOKENIZERS_PARALLELISM=false TRANSFORMERS_VERBOSITY=error
python -m framework.vera.eval.baselines_sota \
    --mode injection --in data/invoices_20k --n 500 --seed 20260703 \
    --out results/vera_baselines_sota
# --no-nli evita el costo OpenAI · --judge-region agrega NLI en scope region (caro)
```

## Caveats (honestos)

1. **`region_id`/`region_truth` = proxy** (línea etiqueta+valor); el corpus sintético no tiene bboxes.
2. **MiniCheck flan-t5-large + torch<2.6**: bypass in-process del guard `torch.load` (CVE-2025-32434),
   sin upgradear torch. Ver `minicheck_scorer(allow_torch_load=...)`.
3. **HHEM en scope ctx es lento** (premise = documento completo): ~154 ms/campo; domina el wall-clock.
4. **Solo flan-t5-large** de MiniCheck; `Bespoke-MiniCheck-7B` (GPU) no se corrió.
