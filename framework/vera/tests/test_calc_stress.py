# Autor: @Raven
"""Regresión del estrés extremo: formato US + corrupción de dígitos de OCR.

Propiedades de seguridad:
  - Formato US (1,234.56) no genera falsos positivos (el parser desambigua locales).
  - NINGUNA factura con un dígito corrupto pasa como COHERENT (se acepta solo lo que se
    pudo verificar): queda INCONSISTENT (cazada) o INCOMPLETE (ilegible, no verificable).

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_stress.py -q
"""
from __future__ import annotations

from framework.vera.calc_consistency import CalcState
from framework.vera.calc_contract import verify_calc
from framework.vera.calc_eval.corpus_stress import build_stress_corpus


def _state(case) -> CalcState:
    r = verify_calc(case.extraction, calc_contract=case.contract)
    return r.state if r is not None else CalcState.INCOMPLETE


def test_formato_us_sin_falsos_positivos():
    cases = [c for c in build_stress_corpus(per_class=30, seed=23)
             if c.fault_class == "us_clean"]
    assert cases
    assert all(_state(c) is CalcState.COHERENT for c in cases)


def test_formato_us_detecta_error():
    cases = [c for c in build_stress_corpus(per_class=30, seed=23)
             if c.fault_class == "us_total_wrong"]
    assert all(_state(c) is CalcState.INCONSISTENT for c in cases)


def test_corrupcion_ocr_nunca_pasa_como_coherente():
    # La propiedad de seguridad central: dato corrupto -> nunca COHERENT.
    corrupt = [c for c in build_stress_corpus(per_class=30, seed=23)
               if c.fault_class.startswith("ocr_")]
    assert corrupt
    for c in corrupt:
        assert _state(c) is not CalcState.COHERENT, (c.fault_class, c.extraction["items"][0])


def test_importe_ilegible_es_incomplete_no_coherent():
    # Un importe de línea sin dígitos legibles -> INCOMPLETE (no verificable), no COHERENT.
    ilegibles = [c for c in build_stress_corpus(per_class=30, seed=23)
                 if c.fault_class == "ocr_unparseable"]
    assert ilegibles
    assert all(_state(c) is CalcState.INCOMPLETE for c in ilegibles)
