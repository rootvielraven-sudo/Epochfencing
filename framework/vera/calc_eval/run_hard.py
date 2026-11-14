# Autor: @Raven
"""Evaluación DURA de la dimensión de consistencia de cálculo.

Extracciones realistas (strings es-AR con ruido de OCR), multi-alícuota (21%+10,5%),
facturas grandes y fallos difíciles → pasan por el parser real (``verify_calc``).
Compara la línea base campo-a-campo (sin chequeo relacional) contra la dimensión y
reporta cuánto mejora.

Correr:  .venv/bin/python -m framework.vera.calc_eval.run_hard
"""
from __future__ import annotations

from ..calc_consistency import CalcState
from ..calc_contract import verify_calc
from .corpus_hard import HARD_CLASSES, build_hard_corpus
from .run_eval import Confusion, _pct


def _predict_inconsistent(case) -> bool:
    r = verify_calc(case.extraction, calc_contract=case.contract)
    return r is not None and r.state is CalcState.INCONSISTENT


def main(per_class: int = 40, seed: int = 11) -> None:
    cases = build_hard_corpus(per_class=per_class, seed=seed)
    print(f"Corpus DURO: {len(cases)} facturas ({per_class}/clase × {len(HARD_CLASSES)} clases), "
          f"seed={seed}\nMulti-alícuota (21%+10,5%), 4-12 líneas, strings es-AR con ruido de OCR.\n")

    # Baseline campo-a-campo: no hay chequeo relacional → nunca marca (recall 0).
    base = Confusion()
    dim = Confusion()
    for c in cases:
        if c.is_faulty and not c.detectable:
            continue  # ciego declarado, va aparte
        pred = _predict_inconsistent(c)
        if c.is_faulty:
            dim.tp += pred; dim.fn += not pred
            base.fn += 1  # el baseline no detecta ninguna inconsistencia relacional
        else:
            dim.fp += pred; dim.tn += not pred
            base.tn += 1

    print("== Baseline (campo-a-campo) vs Dimensión (invariantes) — universo detectable ==")
    print(f"{'cond':<26}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}{'prec':>8}{'rec':>8}{'F1':>8}")
    for name, c in [("A: campo-a-campo", base), ("B: dimensión calc", dim)]:
        print(f"{name:<26}{c.tp:>4}{c.fp:>4}{c.fn:>4}{c.tn:>4}"
              f"{_pct(c.precision):>8}{_pct(c.recall):>8}{_pct(c.f1):>8}")

    print("\n== Detección por clase (dimensión) ==")
    print(f"{'clase':<26}{'n':>4}{'detectadas':>12}{'esperado':>18}")
    for fc in HARD_CLASSES:
        sub = [c for c in cases if c.fault_class == fc]
        det = sum(_predict_inconsistent(c) for c in sub)
        exp = ("INCONSISTENT" if (sub[0].is_faulty and sub[0].detectable)
               else ("COHERENT (ciego)" if sub[0].is_faulty else "COHERENT"))
        flag = ""
        if exp.startswith("INCONSISTENT") and det < len(sub):
            flag = "  <-- escapes"
        if exp == "COHERENT" and det > 0:
            flag = "  <-- FALSO POSITIVO"
        print(f"{fc:<26}{len(sub):>4}{det:>12}{exp:>18}{flag}")

    # Mejora cuantificada.
    caught = dim.tp
    missed_by_baseline = base.fn  # = total positivos (baseline no caza ninguno)
    fp_noise = sum(_predict_inconsistent(c) for c in cases
                   if not c.is_faulty)  # falsos positivos bajo ruido
    blind = [c for c in cases if c.fault_class == "satisfies_invariants"]
    blind_det = sum(_predict_inconsistent(c) for c in blind)

    print("\n== Cuánto mejoró ==")
    print(f"  Errores relacionales que el baseline NO cazó: {missed_by_baseline}")
    print(f"  De esos, la dimensión caza:                   {caught}  "
          f"({_pct(dim.recall).strip()})")
    print(f"  Falsos positivos bajo ruido (clean/redondeo): {fp_noise}  "
          f"(precisión {_pct(dim.precision).strip()})")
    print(f"  F1 baseline → dimensión:                      "
          f"{_pct(base.f1).strip()} → {_pct(dim.f1).strip()}")
    print(f"  Límite declarado (satisface invariantes):     {blind_det}/{len(blind)} detectados "
          f"(esperado 0)")


if __name__ == "__main__":
    main()
