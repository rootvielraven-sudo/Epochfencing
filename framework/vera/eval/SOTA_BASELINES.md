# Table 11 — baselines SOTA + comparación unificada

Extiende la comparación de detectores de Table 11 (`tab:baselines`) con **2 detectores SOTA
pure-classifier de entailment** (HHEM-2.1-Open, MiniCheck), y corre **TODOS los detectores sobre
EXACTAMENTE el mismo sample-set** en una sola pasada (apples-to-apples), sin reabrir el diseño
experimental. Reusa los triples inyectados de la injection/specificity benchmark (`stress_baselines`).

Código: `framework/vera/eval/baselines_sota.py` · Tests: `framework/vera/tests/test_baselines_sota.py`.

## Detectores (todos sobre el mismo N)

| Detector | Scope(s) | Requiere | Determinista | Calls/field |
|---|---|---|---|---|
| VERA | region (anclaje posicional) | — (offline) | sí | 0 |
| Lexical | ctx | — (offline) | sí | 0 |
| LLM-judge (NLI) | ctx (region con `--judge-region`) | OpenAI | no | 1 |
| HHEM-2.1-Open | ctx, region | modelo (caché) | sí | 1 |
| MiniCheck (flan-t5-large) | ctx, region | modelo (caché) | sí | 1 |

- **Claim uniforme**: `"The {field_label} is {claimed_value}."`
- **Scopes**: `ctx` = fuente `S` completa · `region` = región del campo (`region_of`).
- **Threshold** = punto de operación por defecto (`0.5` sobre prob. de *supported*); **NO tuneado por clase**.
- **Región** (fuentes de texto sin bbox): proxy determinista **anclado en el VALOR REAL del campo** —
  la línea de la etiqueta MÁS la línea del valor (garantiza que `region_S` contenga el valor del campo,
  incluso en layouts `Etiqueta:\n valor`; ver `region_of`). Estable a través de las inyecciones.
- **NLI** se recomputa sobre los MISMOS samples (no viene de otra corrida). Batchea por premise idéntico
  → en `ctx`, ~1 llamada OpenAI por documento.

El runner **aborta** si algún `(detector, scope)` no cubre el mismo key-set `(doc_id, field_key,
error_class)` que los samples.

## Métricas (idénticas a Table 11)

Por `error_class` inyectado (`correct` · `single_digit` · `order_of_magnitude` · `field_migration`):

- **Captura (recall)** por clase de error + agregada sobre las clases *wrong*.
- **FP-rate** sobre los controles `correct`.
- **IC Wilson 95%** (`framework.vera.eval.wilson_ci`).
- Columnas extra: `deterministic`, `model_calls_per_field`, `latency_ms_mean`.

## Correr

```bash
pip install "minicheck @ git+https://github.com/Liyan06/MiniCheck.git@main"   # HHEM usa transformers
export TOKENIZERS_PARALLELISM=false TRANSFORMERS_VERBOSITY=error

# Benchmark completo (n docs → ~20 samples/doc). NLI usa OpenAI; HHEM/MiniCheck offline desde caché.
python -m framework.vera.eval.baselines_sota \
    --mode injection --in data/invoices_20k --n 500 --seed 20260703 \
    --out results/vera_baselines_sota

# Flags útiles:
#   --no-nli          omitir LLM-judge (0 costo OpenAI)
#   --judge-region    además correr NLI en scope region (1 llamada/claim; caro)
#   --no-vera --no-lexical --no-hhem --no-minicheck   desactivar detectores
#   --samples <jsonl> reusar triples ya construidos
```

Salidas en `--out`:
- `table11_sota.csv` — una fila por `(detector, scope)`: capture por clase + IC + columnas extra.
- `predictions_sota.csv` — predicción por sample (auditoría): `supported_prob`, `flagged`, ...
- `samples.jsonl` — los triples reusables (value, source, region, truth, error_class).
- `repro_sota.json` — **Data Availability**: `n_docs`/`n_samples`/`seed`/`corpus`, threshold, template,
  y por modelo `repo_id` + `revision/commit` + checkpoint (incluye params del LLM-judge: modelo,
  temperatura=0, prompt).

## Data Availability (por modelo)

| | HHEM-2.1-Open | MiniCheck | LLM-judge (NLI) |
|---|---|---|---|
| repo_id / modelo | `vectara/hallucination_evaluation_model` | `lytang/MiniCheck-Flan-T5-Large` | `gpt-5.4-mini` (temp 0) |
| loader | `AutoModelForSequenceClassification` (`trust_remote_code`) | `minicheck.MiniCheck` | OpenAI chat, JSON yes/no |
| checkpoint | default (opcional `--hhem-revision`) | `flan-t5-large` | — |
| threshold | 0.5 | 0.5 | 0.5 (yes/no) |

Los `revision/commit` efectivos quedan registrados en `repro_sota.json` al correr.

## Operating-point dominance (post-proceso, sin re-correr modelos)

`framework/vera/eval/operating_curve.py` barre el threshold de decisión sobre las
`supported_prob` YA LOGUEADAS en `predictions_sota.csv` (**cero llamadas a HHEM/MiniCheck/OpenAI**)
y responde: ¿existe algún threshold que ponga a un pure-classifier en la esquina de VERA
(captura de field-migration $=1.0$ con FPR $=0.0$)? **No.**

Guard de entrada: aborta si `supported_prob` es binaria (score no logueado) o si `supported_prob<0.5`
no reconstruye EXACTO la columna `flagged`.

| Detector | scope | AUROC (global) | AUROC (migration) | max cap @FPR=0 | min FPR @cap=1.0 |
|---|---|---|---|---|---|
| HHEM-2.1-Open | ctx | 0.841 | 0.723 | 0.000 | 0.888 |
| HHEM-2.1-Open | region | 0.938 | 0.987 | 0.458 | 0.116 |
| MiniCheck | ctx | 0.966 | 0.951 | 0.089 | 0.716 |
| MiniCheck | region | 0.983 | 0.999 | 0.536 | 0.191 |

**Dos números clave (mejor sobre las 4 curvas):** a FPR $=0$ la mejor captura de field-migration es
**0.54**; alcanzar captura $=1.0$ exige un FPR de al menos **0.12**. VERA está en $(0,1)$
(determinista, 0 llamadas): ninguna curva SOTA entra a esa esquina (`vera_corner_reached=False`,
$\varepsilon=10^{-6}$). Las curvas *region* se acercan mucho (captura $\approx 0.996$ a FPR $0.02$)
pero nunca tocan el punto exacto.

Correr (solo pandas/numpy/matplotlib):
```bash
python -m framework.vera.eval.operating_curve   # lee predictions_sota.csv del mismo run
```
Salidas: `threshold_sweep.csv`, `operating_dominance.{csv,json}`, `fig_operating_curve.{pdf,png}`.
Figura del paper: `\ref{fig:operating-curve}` (ROC de field-migration + inset con la esquina).

## Notas

- **MiniCheck + torch<2.6**: el checkpoint flan-t5-large se sirve como `.bin`; se hace un bypass
  in-process del guard `torch.load` (CVE-2025-32434), sin upgradear torch. Ver `minicheck_scorer(allow_torch_load=...)`.
- **`region_id`/`region_truth` = proxy**: el corpus sintético no tiene bboxes; la región es la línea
  etiqueta+valor. Defendible como operacionalización del scope region para fuentes de texto.
