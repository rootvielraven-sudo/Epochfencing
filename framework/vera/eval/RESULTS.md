# VERA — resultados de medición (síntesis honesta)

Todo número sale de `results/**/*.csv` producidos por código ejecutado. Nada se ajusta hacia
ninguna cifra del manuscrito. Extractor LLM: OpenAI `gpt-5.4-mini`, temp 0, seed 20260703.

## Hallazgo metodológico (leer primero)

Intentamos medir el capture end-to-end induciendo alucinaciones reales del LLM sobre facturas
sintéticas. **No es posible de forma confiable:** un modelo de extracción capaz, sobre texto
LIMPIO (que es lo que produce un OCR profesional como Azure DI), extrae los valores de
precisión **etiquetados** con ~0 error, sin importar densidad ni longitud (probado hasta 36
ítems y ~6.000 tokens). Los únicos "errores" que aparecen son de **ambigüedad de definición de
campo** (p.ej. `Comprobante`: el modelo devuelve el tipo "FACTURA B" en vez del número).

**Conclusión:** las alucinaciones de precisión que reporta el paper vienen de **documentos
REALES de producción** (layouts variados, OCR real, ambigüedad genuina), **no sintetizables**.
Por eso la sensibilidad de VERA se mide por **inyección controlada** (abajo), y el end-to-end
sirve para la **especificidad** (falsos positivos a gran N). No se debe presentar un capture
sintético como equivalente al de producción.

---

## A. Sensibilidad por tipo de error — INYECCIÓN controlada
Fuente: `results/vera_stress/detector_stress.csv`. Se inyectan errores realistas conocidos
(dígito, ×10, migración, checksum roto) + controles correctos; se mide si VERA los caza.

| tipo | inyectadas | capturadas | capture | Wilson 95% | correctas | FP |
|---|---|---|---|---|---|---|
| currency_amount | 1200 | 1200 | 1.0 | 0.997–1.0 | 300 | 0 |
| date | 400 | 400 | 1.0 | 0.990–1.0 | 100 | 0 |
| identifier | 800 | 800 | 1.0 | 0.995–1.0 | 200 | 0 |
| percentage | 400 | 400 | 1.0 | 0.990–1.0 | 100 | 0 |
| **OVERALL** | **2800** | **2800** | **1.0** | **0.999–1.0** | **700** | **0** |

→ VERA detecta el **100%** de 2.800 alucinaciones inyectadas con **0 FP** sobre 700 correctos,
en los cuatro tipos. (Texto limpio = techo del mecanismo; ver §D.)

## B. Comparación vs baselines SOTA — INYECCIÓN
Fuente: `results/vera_baselines_inj/comparison.csv`. Mismos triples. SelfCheckGPT **NO APLICA**
(requiere re-extracción del LLM, no verifica un valor dado).

| tipo de error | métrica | **VERA** | NLI-judge | Lexical |
|---|---|---|---|---|
| correct | fp_rate | **0.0** | 0.021 | **0.0** |
| digit_sub | capture | 1.0 | 0.994 | 1.0 |
| magnitude | capture | 1.0 | 1.0 | 1.0 |
| **field migration** | capture | **1.0** | 0.912 | **0.0** |

Migración por tipo: VERA {currency 1.0, date 1.0, identifier 1.0}; NLI {1.0, **0.56**, 1.0};
Lexical {0.0, 0.0, 0.0}.

| detector | determinista | FP | llamadas LLM/campo |
|---|---|---|---|
| VERA | **sí (por construcción)** | 0 | **0** |
| Lexical | sí | 0 | 0 |
| NLI-judge | **no (sin garantía)** | 2.1% | **1** |

→ El diferenciador es la **migración de campo**: **VERA 1.0 vs Lexical 0.0** (el baseline trivial
falla del todo). VERA también supera al LLM-judge (0.91; **0.56 en fechas**) y es **determinista,
0 FP, 0 llamadas LLM**. En **fabricaciones (dígito/magnitud) todos empatan** — VERA no gana ahí.

## C. Especificidad — END-TO-END 20k (extracciones reales del LLM)
Fuente: `results/vera_20k/*.csv`. 20.000 facturas, **139.999+ campos**, extracción real gpt-5.4-mini.

| N | TP | FP | FN | TN | capture | fp_rate (Wilson) | human_review |
|---|---|---|---|---|---|---|---|
| 140000 | 5533 | **0** | 0 | 134467 | 1.0 | **0.0 (0–0)** | 3.95% |

→ Sobre **~140.000 extracciones reales**, VERA tuvo **0 falsos positivos**. Los 5.533 flagueados
son la confusión genuina de `Comprobante` (tipo vs número), que VERA surfacea para validación.
**El valor honesto de este run es la especificidad (0 FP a gran N), no un "capture".**

## D. Caveats declarables (van al paper)
1. Texto limpio = **techo** del mecanismo; el 96.1% del paper es sobre OCR real ruidoso (más
   difícil), **no comparable** con estas cifras sintéticas.
2. El capture de migración de VERA **depende de etiquetas distintas**; con campos co-etiquetados
   ("CUIT:" para emisor y receptor) cae a ~0.33 — limitación de la proximidad-por-etiqueta.
3. VERA cubre entidades de **precisión** (números/importes/códigos/fechas/%), **no texto libre**.

## Reproducibilidad
Generador (seed 20260703), runners e inyección: `framework/vera/eval/` (generate_invoices.py,
run_eval.py, stress_vera.py, baselines.py, stress_baselines.py). Sin datos reales de cliente.
