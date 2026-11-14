# Autor: @Raven
"""Conjunto de pruebas de la INTEGRACIÓN compuesta de VERA:
  - veredicto unificado (cobertura + cálculo → un estado, el más severo gana);
  - atribución OCR-vs-LLM-vs-DOCUMENTO usando la evidencia OCR de VERA;
  - end-to-end sobre factura real + serialización.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_vera_integration_calc.py -q
"""
from __future__ import annotations

import copy
import json

import pytest

from framework.vera.calc_consistency import (
    Invoice, LineItem, Obs, Role, TaxComponent, check_calc_consistency,
    to_cents, to_fraction,
)
from framework.vera.calc_contract import verify_calc
from framework.vera.calc_diagnosis import (
    ORIGIN_DOC, ORIGIN_INDET, ORIGIN_LLM, ORIGIN_OCR, diagnose,
)
from framework.vera.calc_eval.inject_real import REAL
from framework.vera.vera_verdict import (
    calc_state, combine, coverage_state, overall_state,
)


def _line(lid, q, p, a):
    return LineItem(lid,
                    Obs(f"{lid}.q", Role.QTY, to_fraction(q)),
                    Obs(f"{lid}.p", Role.UNIT_PRICE, to_cents(p)),
                    Obs(f"{lid}.a", Role.LINE_AMOUNT, to_cents(a)))


# ───────────────────────────── veredicto unificado ─────────────────────────

@pytest.mark.parametrize("cov,cal,expected", [
    ("clean", "clean", "clean"),
    ("clean", "review", "review"),
    ("review", "clean", "review"),
    ("clean", "blocked", "blocked"),
    ("blocked", "clean", "blocked"),
    ("review", "blocked", "blocked"),
    ("blocked", "review", "blocked"),
    (None, "blocked", "blocked"),      # solo cálculo activo
    ("review", None, "review"),         # solo cobertura activa
    (None, None, "clean"),              # ninguna
])
def test_overall_state_el_mas_severo_gana(cov, cal, expected):
    assert overall_state(coverage=cov, calc=cal) == expected


def test_coverage_state_mapea():
    assert coverage_state(True, False) == "blocked"
    assert coverage_state(False, True) == "review"
    assert coverage_state(False, False) == "clean"


def test_calc_state_mapea():
    assert calc_state("inconsistent") == "blocked"
    assert calc_state("incomplete") == "review"
    assert calc_state("coherent") == "clean"
    # dimensión NO corrió (VERA_CALC apagado): sin señal (NO 'clean')
    assert calc_state(None) is None


def test_calc_state_error_es_review_fail_closed():
    """Un verificador que FALLÓ nunca certifica 'clean' (fail-closed): ERROR → review."""
    from framework.vera.calc_consistency import CalcState
    assert calc_state(CalcState.ERROR.value) == "review"
    assert calc_state("error") == "review"
    # estado desconocido de un verificador que corrió → review, nunca clean
    assert calc_state("algo_raro") == "review"


def test_calc_state_distingue_apagado_de_error():
    """None (no corrió) ≠ ERROR (corrió y falló): señales distintas."""
    # apagado: no aporta señal → el overall lo ignora
    assert overall_state(coverage="clean", calc=calc_state(None)) == "clean"
    # error: fail-closed → eleva a 'review' aunque la cobertura esté limpia
    assert overall_state(coverage="clean", calc=calc_state("error")) == "review"


def test_combine_coverage_mas_calc_error_da_review():
    v = combine(coverage="clean", calc=calc_state("error"))
    assert v["state"] == "review" and v["blocked"] is False
    assert v["dimensions"] == {"coverage": "clean", "calc": "review"}
    # error no bloquea si no hay inconsistencia demostrada, pero sí fuerza revisión
    v2 = combine(coverage="blocked", calc=calc_state("error"))
    assert v2["state"] == "blocked"  # el más severo gana


def test_combine_bloquea_y_lleva_diagnostico():
    diag = {"summary": "x"}
    v = combine(coverage="clean", calc="blocked", calc_diagnosis=diag)
    assert v["state"] == "blocked" and v["blocked"] is True
    assert v["dimensions"] == {"coverage": "clean", "calc": "blocked"}
    assert v["calc_diagnosis"] == diag
    # el diagnóstico NO viaja si el cálculo no bloqueó
    v2 = combine(coverage="review", calc="clean", calc_diagnosis=diag)
    assert "calc_diagnosis" not in v2


def test_combine_serializable():
    v = combine(coverage="blocked", calc="review")
    assert json.loads(json.dumps(v))["state"] == "blocked"


# ───────────────────────────── atribución con evidencia OCR ────────────────

def _amount_origin(inv, ocr=None):
    d = diagnose(check_calc_consistency(inv), ocr_text=ocr)
    rep = next(r for r in d["conflicts"][0]["repairs"] if r["role"] == "amount")
    return rep["origin"], rep["reason"]


def test_atribucion_error_llm_cuando_ocr_tiene_el_valor_correcto():
    # 2*60=120, el LLM puso 1200; el OCR dice 120 → el LLM divergió del OCR.
    inv = Invoice(lines=[_line("L0", 2, 60, 1200)])
    ocr = "cantidad 2 precio 60,00 importe 120,00 total 120,00"
    origin, reason = _amount_origin(inv, ocr)
    assert origin == ORIGIN_LLM
    assert "OCR muestra 120,00" in reason


def test_atribucion_error_ocr_cuando_el_valor_extraido_esta_en_el_ocr_con_patron():
    # El OCR TAMBIÉN muestra 1.200 (leyó mal en origen) y hay patrón ×10 → ERROR_OCR.
    inv = Invoice(lines=[_line("L0", 2, 60, 1200)])
    ocr = "cantidad 2 precio 60,00 importe 1.200,00"   # el OCR muestra el valor malo
    origin, _ = _amount_origin(inv, ocr)
    assert origin == ORIGIN_OCR


def test_atribucion_error_documento_ocr_fiel_sin_patron():
    # El OCR muestra el mismo importe raro (sin patrón de dígito simple) → DOCUMENTO.
    inv = Invoice(lines=[_line("L0", 7, 13, 137)])   # 7*13=91, no 137
    ocr = "cantidad 7 precio 13,00 importe 137,00"   # OCR fiel al 137
    origin, _ = _amount_origin(inv, ocr)
    assert origin == ORIGIN_DOC


def test_atribucion_indeterminado_sin_ocr_sin_patron():
    inv = Invoice(lines=[_line("L0", 7, 13, 137)])
    origin, _ = _amount_origin(inv, None)
    assert origin == ORIGIN_INDET


def test_atribucion_ocr_por_patron_sin_evidencia():
    # sin OCR pero con patrón ×10 → ERROR_OCR (heurístico)
    inv = Invoice(lines=[_line("L0", 2, 60, 1200)])
    origin, _ = _amount_origin(inv, None)
    assert origin == ORIGIN_OCR


# ───────────────────────────── end-to-end ──────────────────────────────────

def test_end_to_end_factura_real_con_ocr_y_veredicto():
    # Factura real, adulterada en una línea; el OCR (texto real-ish) tiene el valor bueno.
    f = REAL[0]  # Factura B 00997224 (2 líneas, total 59.800)
    bad = copy.deepcopy(f["extraction"])
    bad["items"][1]["importe"] = "5.830,00"   # 55.830 -> 5.830 (dígito perdido)
    r = verify_calc(bad, calc_contract=f["contract"])
    assert r.state.value == "inconsistent"

    ocr = ("Gastos de envio 3.970,00  Disco Solido SSD 55.830,00  TOTAL 59.800,00")
    diag = diagnose(r, ocr_text=ocr)
    # culpable: el importe de L1 debería ser 55.830,00, y el OCR lo respalda → ERROR_LLM
    cand = [c for c in diag["candidate_repairs"]]
    assert any(c["suggested"] == "55.830,00" for c in cand)
    llm_attr = [c for c in cand if c["origin"] == ORIGIN_LLM and c["suggested"] == "55.830,00"]
    assert llm_attr, diag

    # veredicto unificado con cobertura limpia + cálculo bloqueado → blocked + diagnóstico
    verdict = combine(coverage="clean", calc=calc_state(r.state.value), calc_diagnosis=diag)
    assert verdict["state"] == "blocked"
    assert verdict["calc_diagnosis"]["applicable"] is True
    assert json.loads(json.dumps(verdict))  # serializable
