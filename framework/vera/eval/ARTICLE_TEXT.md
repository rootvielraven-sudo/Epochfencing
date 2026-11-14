# Texto para el artículo (Claude web) — validación reproducible de VERA

Todo número sale de `results/**/*.csv` (código ejecutado, extractor OpenAI gpt-5.4-mini temp 0,
seed 20260703). Nada ajustado hacia el manuscrito.

> ⚠️ **Guardarraíl (crítico para Claude web):** estos experimentos son **sintéticos, texto
> limpio, reproducibles**. Miden (a) la **sensibilidad** de VERA por tipo de error (inyección
> controlada) y (b) su **especificidad** (0 FP) sobre extracciones reales. **NO reproducen ni
> se comparan con el 96.1 % del paper** (producción, OCR real, anotación humana). No escribir
> "96.1 %" cerca de estas cifras. Presentar como el **paquete de reproducibilidad** que pidieron
> los revisores (R1.2/R1.3/R2/R3.4) + **evidencia controlada de detección**, complementario a los
> números confidenciales de producción.
>
> **Nota honesta obligatoria:** un LLM de extracción capaz, sobre texto limpio, casi no alucina
> en campos etiquetados (verificado hasta 36 ítems / ~6k tokens); las alucinaciones de precisión
> reales provienen de documentos de producción no sintetizables. Por eso la **sensibilidad se mide
> por inyección** y el end-to-end aporta la **especificidad**.

---

## Subsección LaTeX: "Reproducible Validation of VERA"

```latex
\subsection{Reproducible Validation of VERA}
\label{sec:synthetic-eval}

To make VERA's behaviour independently reproducible without releasing confidential
data, we evaluate it on synthetic Spanish invoices (fixed seed) with clean text,
the setting a professional OCR engine produces. Two complementary measurements are
reported: detector \emph{sensitivity} by error class via controlled injection, and
\emph{specificity} (false-positive rate) on real LLM extractions. We stress that a
capable extractor on clean, well-labelled fields rarely hallucinates
(regardless of document length or density in our tests), so genuine
precision-hallucination rates cannot be synthesised; this is why sensitivity is
measured by injection rather than end-to-end.

\paragraph{Sensitivity (injected errors).}
We inject realistic errors of each class---single-digit substitution,
order-of-magnitude shift, field migration (a value copied from another field), and
checksum-breaking edits---plus correct controls, and measure detection
(Table~\ref{tab:vera-sens}). VERA flags $2{,}800/2{,}800$ injected errors
($1.00$, 95\% CI $0.999$--$1.0$) with $0$ false positives on $700$ correct values,
across all four entity types.

\begin{table}[h]
\caption{VERA sensitivity/specificity on injected errors (clean synthetic fields).}
\label{tab:vera-sens}
\centering\small
\begin{tabular}{lrrcrr}
\toprule
Entity type & Injected & Captured & Capture (95\% CI) & Correct & FP \\
\midrule
Currency amount & 1{,}200 & 1{,}200 & $1.00$ (0.997--1.0) & 300 & 0 \\
Date            &   400 &   400 & $1.00$ (0.990--1.0) & 100 & 0 \\
Identifier      &   800 &   800 & $1.00$ (0.995--1.0) & 200 & 0 \\
Percentage      &   400 &   400 & $1.00$ (0.990--1.0) & 100 & 0 \\
\midrule
\textbf{Overall} & \textbf{2{,}800} & \textbf{2{,}800} & \textbf{1.00 (0.999--1.0)} & \textbf{700} & \textbf{0} \\
\bottomrule
\end{tabular}
\end{table}

\paragraph{Comparison against baselines.}
On the same injected triples we compare VERA with an LLM-judge entailment baseline
(NLI/RAGTruth-style) and a lexical-presence baseline; SelfCheckGPT is not
applicable (its signal requires re-extraction, not verification of a given value).
On fabrication errors all detectors are near-ceiling. The separation is
\textbf{field migration}, where the wrong value \emph{is} present in the document:
lexical presence fails entirely ($0.00$), the LLM-judge is competitive overall
($0.91$) but unreliable per class ($0.56$ on dates) and non-deterministic, whereas
VERA's region anchoring captures all migrations ($1.00$) deterministically, with
zero false positives and no model call (Table~\ref{tab:baselines}).

\begin{table}[h]
\caption{Detectors on injected errors (100 documents). Capture per class; FP on
correct values.}
\label{tab:baselines}
\centering\small
\begin{tabular}{llccc}
\toprule
Error class & Metric & VERA & LLM-judge (NLI) & Lexical \\
\midrule
Correct (specificity) & FP rate & $0.00$ & $0.021$ & $0.00$ \\
Single-digit sub.\    & Capture & $1.00$ & $0.994$ & $1.00$ \\
Order-of-magnitude    & Capture & $1.00$ & $1.00$  & $1.00$ \\
\textbf{Field migration} & Capture & $\mathbf{1.00}$ & $0.912$ & $\mathbf{0.00}$ \\
\midrule
\multicolumn{2}{l}{Deterministic}       & yes & \textbf{no} & yes \\
\multicolumn{2}{l}{Model calls / field} & \textbf{0} & 1 & \textbf{0} \\
\bottomrule
\end{tabular}
\end{table}

\paragraph{Specificity at scale (real extractions).}
Over $20{,}000$ documents and $\approx 140{,}000$ real LLM-extracted precision
fields, VERA raised \textbf{zero false positives} (FP rate $0.0$, 95\% CI
$0$--$0$; human-review rate $3.95\%$). The flagged fields were a genuine
field-name confusion (invoice type extracted instead of number), which VERA
correctly surfaced for human validation. This large-scale run establishes VERA's
false-positive rate, not a capture figure.

\paragraph{Scope and honesty.} These results characterise VERA in the clean-source
regime and on precision entities (numbers, amounts, codes, dates); they are a
\emph{ceiling}, not the production capture on noisy real OCR
(\S\ref{sec:vera}). VERA's migration capture further depends on distinct field
labels: when a document co-labels two fields (e.g.\ ``CUIT:'' for issuer and
client) capture drops to $\approx 0.33$; this is a stated limitation of
label-proximity anchoring.
```

## Nota de reproducibilidad (Data Availability / apéndice)

```latex
Fully reproducible: an invoice generator (fixed seed 20260703), an injection-based
detector harness, an end-to-end LLM-extraction runner, and metric scripts (with
Wilson 95\% intervals) are provided; no real client data is used. The extractor is
a single mid-tier LLM at temperature~0.
```

## Mapeo a la carta de respuesta (RESPONSE_TO_REVIEWERS)
- **R1.3 / R2 / R3.5 (matrices de confusión + baselines):** Tablas \ref{tab:vera-sens} y
  \ref{tab:baselines}, N por clase e IC Wilson; VERA vs LLM-judge vs lexical; se aclara que
  SelfCheckGPT no aplica a verificación de un valor dado.
- **R1.2 / R3.4 (reproducibilidad):** generador con seed + scripts, sin datos reales.

## Cifras exactas (trazabilidad)
| Métrica | Valor | CSV |
|---|---|---|
| Sensibilidad (inyección) | 2.800/2.800 capturadas; 0 FP/700 | vera_stress/detector_stress.csv |
| Migración: VERA/NLI/Lexical | 1.00 / 0.912 / 0.00 | vera_baselines_inj/comparison.csv |
| Especificidad end-to-end | 0 FP sobre ~140.000 campos (Wilson 0–0) | vera_20k/overall.csv |
| human-review (20k) | 3.95% | vera_20k/overall.csv |

**No** escribir que esto reproduce el 96.1%, ni comparar-y-ajustar hacia el manuscrito.
```
