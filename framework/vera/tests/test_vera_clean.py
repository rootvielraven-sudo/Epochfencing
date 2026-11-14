# Autor: @Raven
"""Clean stage: normalización OCR conservadora (números sí, prosa no)."""
from __future__ import annotations

from framework.vera import Source, clean_ocr_text, load_contract, verify


def test_clean_fixes_numeric_preserves_prose():
    # 'l'->1 en token numérico; prosa intacta (García, Servico no tienen dígitos)
    assert clean_ocr_text("Total: l.234,56 García Servico") == "Total: 1.234,56 García Servico"
    # CUIT con O->0
    assert clean_ocr_text("CUIT emisor: 2O-57479675-O") == "CUIT emisor: 20-57479675-0"
    # una palabra con un dígito suelto (mayoría letras) NO se toca
    assert clean_ocr_text("Oficina3") == "Oficina3"


def test_clean_recovers_cuit_in_vera():
    contract = load_contract({"entities": [{"name": "CUIT emisor", "type": "identifier", "fmt": "cuit"}]})
    ocr = "CUIT emisor: 2O-57479675-O\n"        # OCR confundió 0->O
    out = {"CUIT_emisor": "20-57479675-0"}
    # sin clean: el token con letras no es un CUIT válido → no ancla
    r0 = verify(out, Source.ocr(ocr), contract)
    # con clean: recupera el CUIT y ancla
    r1 = verify(out, Source.ocr(ocr, clean=True), contract)
    assert r1.records[0].verdict.value == "supported"
    assert r0.records[0].verdict.value != "supported"   # el clean aporta valor


def test_clean_off_by_default():
    contract = load_contract({"entities": [{"name": "Total", "type": "number"}]})
    ocr = "Total: 1.234,56\n"
    # limpio ya anclaba; con o sin clean el correcto sigue SUPPORTED
    assert verify({"Total": "1234.56"}, Source.ocr(ocr), contract).records[0].verdict.value == "supported"
