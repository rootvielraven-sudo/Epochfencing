# Autor: @Raven
"""Tests del diagnóstico de consistencia de cálculo (fase 2, sin Z3):
reparaciones candidatas, conflicto mínimo (hitting set) y atribución.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_diagnosis.py -q
"""
from __future__ import annotations

import copy

from framework.vera.calc_consistency import (
    Invoice, LineItem, Obs, Role, check_calc_consistency, to_cents, to_fraction,
)
from framework.vera.calc_contract import verify_calc
from framework.vera.calc_diagnosis import ORIGIN_OCR, diagnose
from framework.vera.calc_eval.inject_real import REAL


def _line(lid, q, p, a):
    return LineItem(lid,
                    Obs(f"{lid}.q", Role.QTY, to_fraction(q)),
                    Obs(f"{lid}.p", Role.UNIT_PRICE, to_cents(p)),
                    Obs(f"{lid}.a", Role.LINE_AMOUNT, to_cents(a)))


def test_ejemplo_del_paper_tres_reparaciones_dos_plausibles():
    # cantidad=3, precio=40, importe=150 (el ejemplo del §4 del paper).
    d = diagnose(check_calc_consistency(Invoice(lines=[_line("L0", 3, 40, 150)])))
    reps = {r["role"]: r for r in d["conflicts"][0]["repairs"]}
    assert reps["amount"]["suggested"] == "120,00" and reps["amount"]["plausible"]
    assert reps["price"]["suggested"] == "50,00" and reps["price"]["plausible"]
    # qty = 15/4 = 3,75 → NO entera → implausible (como dice el paper)
    assert reps["qty"]["plausible"] is False
    # un conflicto aislado con 2 reparaciones plausibles → NO hay culpable único
    assert d["single_culprit"] is False
    plaus = {r["role"] for r in d["candidate_repairs"]}
    assert plaus == {"amount", "price"}


def test_culpable_unico_cross_invariante():
    # un importe de línea mal rompe línea + subtotal → el culpable mínimo es esa 1 obs
    inv = Invoice(lines=[_line("L0", 2, 50, 100), _line("L1", 3, 40, 120)],
                  subtotal=Obs("sub", Role.SUBTOTAL, to_cents(220)),
                  total=Obs("tot", Role.TOTAL, to_cents(220)))
    inv.lines[1].amount.value = to_cents(1200)   # 120 -> 1200 (dígito agregado)
    d = diagnose(check_calc_consistency(inv))
    assert d["single_culprit"] is True
    assert d["minimal_culprits"] == ["L1.a"]
    rep = d["candidate_repairs"][0]
    assert rep["obs_id"] == "L1.a" and rep["suggested"] == "120,00"
    assert rep["origin"] == ORIGIN_OCR   # factor ×10


def test_atribucion_patrones_ocr():
    # dígito transpuesto en un importe de línea → ERROR_OCR "transpuestos"
    inv = Invoice(lines=[_line("L0", 1, 1234, 1243)])  # 1234 -> 1243 (swap de 2 dígitos)
    d = diagnose(check_calc_consistency(inv))
    amount_rep = next(r for r in d["conflicts"][0]["repairs"] if r["role"] == "amount")
    assert amount_rep["origin"] == ORIGIN_OCR
    assert "transpuest" in amount_rep["reason"]


def test_tax_product_reparaciones():
    from framework.vera.calc_consistency import TaxComponent
    from fractions import Fraction
    inv = Invoice(taxes=[TaxComponent(
        "iva", Obs("b", Role.TAX_BASE, to_cents(1000)),
        Obs("r", Role.TAX_RATE, to_fraction(Fraction(21, 100))),
        Obs("a", Role.TAX_AMOUNT, to_cents(250)))])  # 1000*0.21=210, no 250
    d = diagnose(check_calc_consistency(inv))
    reps = {r["role"]: r for r in d["conflicts"][0]["repairs"]}
    assert reps["amount"]["suggested"] == "210,00"   # base*rate


def test_no_aplica_si_coherente():
    d = diagnose(check_calc_consistency(Invoice(lines=[_line("L0", 3, 40, 120)])))
    assert d["applicable"] is False


def test_diagnostico_sobre_factura_real():
    # adultera un importe de línea de una factura REAL y verifica que el diagnóstico
    # lo señale con reparación plausible.
    f = REAL[2]  # multi-alícuota 01034901
    bad = copy.deepcopy(f["extraction"])
    bad["items"][0]["importe"] = "99.999,00"   # 5.175 -> 99.999 (mal)
    r = verify_calc(bad, calc_contract=f["contract"])
    d = diagnose(r)
    assert d["applicable"] is True
    # L0.amount debe aparecer como reparación candidata hacia el valor real (5.175,00)
    all_reps = [rep for c in d["conflicts"] for rep in c["repairs"]]
    l0 = [rep for rep in all_reps if rep["obs_id"] == "L0.amount"]
    assert l0 and any(rep["suggested"] == "5.175,00" for rep in l0)
