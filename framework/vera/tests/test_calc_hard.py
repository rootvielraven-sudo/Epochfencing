# Autor: @Raven
"""Regresión del corpus DURO: extracciones realistas (strings es-AR con ruido de OCR),
multi-alícuota (21%+10,5%), facturas grandes y fallos difíciles, vía el parser real.

Bloquea: 100% recall sobre lo detectable, 0 falsos positivos bajo ruido, y el límite
declarado (satisface invariantes) NO se detecta.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_hard.py -q
"""
from __future__ import annotations

from framework.vera.calc_consistency import CalcState
from framework.vera.calc_contract import verify_calc
from framework.vera.calc_eval.corpus_hard import HARD_CLASSES, build_hard_corpus


def _inconsistent(case) -> bool:
    r = verify_calc(case.extraction, calc_contract=case.contract)
    return r is not None and r.state is CalcState.INCONSISTENT


def test_corpus_duro_recall_total_y_cero_falsos_positivos():
    cases = build_hard_corpus(per_class=20, seed=11)
    fn = fp = tp = tn = 0
    for c in cases:
        if c.is_faulty and not c.detectable:
            continue  # ciego, aparte
        pred = _inconsistent(c)
        if c.is_faulty:
            tp += pred; fn += not pred
        else:
            fp += pred; tn += not pred
    assert fn == 0, "debe cazar TODOS los fallos relacionales detectables (incluso con ruido)"
    assert fp == 0, "cero falsos positivos bajo ruido de OCR + redondeo multi-alícuota"
    assert tp > 0 and tn > 0


def test_corpus_duro_limite_ciego_no_se_detecta():
    cases = [c for c in build_hard_corpus(per_class=20, seed=11)
             if c.fault_class == "satisfies_invariants"]
    assert cases
    assert sum(_inconsistent(c) for c in cases) == 0  # SAT no prueba veracidad


def test_corpus_duro_clases_dificiles_se_cazan():
    # cada clase difícil (no-ciega, no-coherente) debe detectarse al 100%
    hard_faults = [fc for fc in HARD_CLASSES
                   if fc not in ("clean_noisy", "rounding_legit", "off_by_one_cent",
                                 "satisfies_invariants")]
    cases = build_hard_corpus(per_class=15, seed=5)
    for fc in hard_faults:
        sub = [c for c in cases if c.fault_class == fc]
        det = sum(_inconsistent(c) for c in sub)
        assert det == len(sub), f"{fc}: detectó {det}/{len(sub)}"
