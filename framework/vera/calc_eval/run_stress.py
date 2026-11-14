# Autor: @Raven
"""Estrés extremo: formato US + corrupción de dígitos de OCR.

Mide dos cosas:
  - Formato US: falsos positivos sobre facturas coherentes en convención US.
  - Corrupción de OCR: cuántas facturas con un dígito corrupto pasan PELIGROSAMENTE
    como COHERENT (aceptadas con datos mal), vs el resultado seguro INCONSISTENT
    (cazada) o INCOMPLETE (no verificable).

Correr:  .venv/bin/python -m framework.vera.calc_eval.run_stress
"""
from __future__ import annotations

from collections import Counter

from ..calc_consistency import CalcState
from ..calc_contract import verify_calc
from .corpus_stress import STRESS_CLASSES, build_stress_corpus


def _state(case) -> CalcState:
    r = verify_calc(case.extraction, calc_contract=case.contract)
    return r.state if r is not None else CalcState.INCOMPLETE


def main(per_class: int = 60, seed: int = 23) -> None:
    cases = build_stress_corpus(per_class=per_class, seed=seed)
    print(f"Corpus ESTRÉS: {len(cases)} facturas ({per_class}/clase × {len(STRESS_CLASSES)}), "
          f"seed={seed}\nFormato US (1,234.56) + corrupción de dígitos de OCR.\n")

    print(f"{'clase':<22}{'n':>4}{'COHERENT':>10}{'INCONS':>8}{'INCOMPL':>9}   veredicto")
    dangerous = 0
    us_fp = 0
    for fc in STRESS_CLASSES:
        sub = [c for c in cases if c.fault_class == fc]
        cnt = Counter(_state(c).value for c in sub)
        coh = cnt.get("coherent", 0)
        inc = cnt.get("inconsistent", 0)
        incompl = cnt.get("incomplete", 0)

        faulty = sub[0].is_faulty
        if not faulty:  # us_clean
            verdict = "OK" if inc == 0 else f"{inc} FALSOS POSITIVOS"
            us_fp += inc
        else:
            # para corruptas: COHERENT es el resultado PELIGROSO (aceptar basura)
            if coh == 0:
                verdict = "OK (ninguna pasa como limpia)"
            else:
                verdict = f"{coh} PELIGROSAS (pasan como COHERENT)"
                dangerous += coh
        print(f"{fc:<22}{len(sub):>4}{coh:>10}{inc:>8}{incompl:>9}   {verdict}")

    corrupt = [c for c in cases if c.fault_class.startswith("ocr_")]
    print("\n== Resumen de seguridad ==")
    print(f"  Formato US — falsos positivos:               {us_fp} "
          f"(el parser desambigua 1.234,56 ≡ 1,234.56)")
    print(f"  Facturas corruptas de OCR:                   {len(corrupt)}")
    print(f"  De esas, PELIGROSAS (pasan como COHERENT):   {dangerous}")
    safe = len(corrupt) - dangerous
    print(f"  Seguras (INCONSISTENT o INCOMPLETE):         {safe} "
          f"({100*safe/len(corrupt):.1f}%)")
    print("\n  Nota: INCOMPLETE = el importe no se pudo leer → no se verifica (honesto),")
    print("  no se acepta como correcto. Solo COHERENT con datos corruptos sería una fuga.")


if __name__ == "__main__":
    main()
