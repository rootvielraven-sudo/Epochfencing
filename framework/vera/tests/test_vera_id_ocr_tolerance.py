# Autor: @Raven
"""Tolerancia OCR NO-DESTRUCTIVA en anclaje de identificadores (fold simétrico).
Fuente degradada → REVIEW (recuperado, no INVENTED, no falso SUPPORTED). Nunca reescribe el valor.
Se testea anchor_identifier directo (aísla el fold; verify añade proximidad de etiqueta)."""
from framework.vera.vera_identifiers import anchor_identifier as A
from framework.vera.vera_types import EntityVerdict as V

def test_clean_ocr_exact_supported():
    assert A("DCS30280008176920235714843", "Nº DI: DCS30280008176920235714843") == V.SUPPORTED

def test_degraded_generic_folds_to_review():
    # S->5 degradado en el prefijo → recuperado a REVIEW, no INVENTED
    assert A("DCS30280008176920235714843", "Nº DI: DC530280008176920235714843") == V.REVIEW

def test_cuit_degraded_review_not_supported():
    assert A("20-57479675-0", "CUIT: 2O-57479675-O", fmt="cuit") == V.REVIEW

def test_cuit_clean_supported():
    assert A("20-57479675-0", "CUIT: 20-57479675-0", fmt="cuit") == V.SUPPORTED

def test_absent_stays_invented_no_false_fold():
    assert A("20-11111111-2", "CUIT: 20-57479675-0", fmt="cuit") == V.INVENTED

def test_cif_letter_prefix_not_corrupted():
    # B83667725 (CIF, B real) ancla exacto sobre OCR limpio → SUPPORTED, sin tratar la B como error
    assert A("B83667725", "CIF/NIF: B83667725") == V.SUPPORTED
