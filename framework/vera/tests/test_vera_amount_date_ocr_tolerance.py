# Autor: @Raven
"""Tolerancia OCR NO-DESTRUCTIVA en anclaje de IMPORTES y FECHAS (mismo fold que identificadores).
Fuente degradada → REVIEW (recuperado, no INVENTED, no falso SUPPORTED). Nunca reescribe el valor:
clean_ocr_text solo pliega tokens con dígitos y se usa como clave de comparación (fuente cruda)."""
from decimal import Decimal

from framework.vera.vera_coverage import _anchor_number_role, _ocr_magnitudes
from framework.vera.vera_dates import anchor_date as D
from framework.vera.vera_types import EntityVerdict as V


def _amount(llm, ocr, label="Importe"):
    return _anchor_number_role(llm, ocr, label, _ocr_magnitudes(ocr))[0]


# ---- importes ---------------------------------------------------------------
def test_amount_clean_supported():
    assert _amount("1.234,56", "Importe total: 1.234,56 EUR") == V.SUPPORTED


def test_amount_degraded_digit_folds_to_review():
    # '1' leído como 'l' en el importe → recuperado a REVIEW, no INVENTED
    assert _amount("1.234,56", "Importe total: l.234,56 EUR") == V.REVIEW


def test_amount_absent_stays_invented_no_false_fold():
    assert _amount("1.234,56", "Importe total: 9.999,99 EUR") == V.INVENTED


# ---- fechas -----------------------------------------------------------------
def test_date_clean_unambiguous_supported():
    assert D("2024-03-25", "Fecha: 25/03/2024") == V.SUPPORTED


def test_date_degraded_O_for_zero_folds_to_review():
    # 'O3' (letra O) por '03' en el mes → recuperado a REVIEW, no INVENTED
    assert D("2024-03-25", "Fecha: 25/O3/2024") == V.REVIEW


def test_date_degraded_l_for_one_folds_to_review():
    assert D("2024-03-15", "Fecha: l5/03/2024") == V.REVIEW


def test_date_absent_stays_invented_no_false_fold():
    assert D("2024-03-25", "Fecha: 01/01/2020") == V.INVENTED