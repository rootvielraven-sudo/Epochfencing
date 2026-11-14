# Autor: @Raven
"""Tests de la dimensión de consistencia de cálculo (condición B, sin solver).

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_consistency.py -q
"""
from __future__ import annotations

from fractions import Fraction

from framework.vera.calc_consistency import (
    CalcState, Invoice, LineItem, Obs, Role, TaxComponent,
    attach_calc, check_calc_consistency, to_cents, to_fraction,
)
from framework.vera.calc_eval.corpus import FAULT_CLASSES, build_corpus
from framework.vera.calc_eval.run_eval import mvp_predict


def _line(lid, qty, price, amount):
    return LineItem(
        line_id=lid,
        qty=Obs(f"{lid}.q", Role.QTY, to_fraction(qty)),
        unit_price=Obs(f"{lid}.p", Role.UNIT_PRICE, to_cents(price)),
        amount=Obs(f"{lid}.a", Role.LINE_AMOUNT, to_cents(amount)),
    )


def _tax(base, rate, amount):
    return TaxComponent(
        tax_id="iva",
        base=Obs("t.b", Role.TAX_BASE, to_cents(base)),
        rate=Obs("t.r", Role.TAX_RATE, to_fraction(rate)),
        amount=Obs("t.a", Role.TAX_AMOUNT, to_cents(amount)),
    )


# ───────────────────────────── detección básica ────────────────────────────

def test_linea_coherente():
    inv = Invoice(lines=[_line("L0", 3, 40, 120)])
    r = check_calc_consistency(inv)
    assert r.state is CalcState.COHERENT
    assert r.invariants_checked == 1
    assert r.ok


def test_linea_inconsistente_detecta_y_localiza():
    # 3 * 40 = 120, no 150
    inv = Invoice(lines=[_line("L0", 3, 40, 150)])
    r = check_calc_consistency(inv)
    assert r.state is CalcState.INCONSISTENT
    assert r.demonstrated_inconsistent
    v = r.violations[0]
    assert "line[L0]" in v.rule
    # localización: las 3 observaciones de la línea participan del conflicto
    assert {"L0.q", "L0.p", "L0.a"} <= set(v.obs_ids)


def test_subtotal_caza_fila_duplicada():
    # dos líneas iguales pero subtotal impreso de UNA sola -> Σamount > subtotal
    inv = Invoice(
        lines=[_line("L0", 2, 50, 100), _line("DUP", 2, 50, 100)],
        subtotal=Obs("sub", Role.SUBTOTAL, to_cents(100)),
    )
    r = check_calc_consistency(inv)
    assert r.state is CalcState.INCONSISTENT
    assert any("subtotal" in v.rule for v in r.violations)


def test_iva_incorrecto():
    inv = Invoice(taxes=[_tax(1000, Fraction(21, 100), 250)])  # 1000*0.21=210, no 250
    r = check_calc_consistency(inv)
    assert r.state is CalcState.INCONSISTENT
    assert any("tax[iva]" in v.rule for v in r.violations)


def test_aritmetica_exacta_sin_drift_de_float():
    # 0.1 + 0.2 != 0.3 en float; aquí debe ser EXACTO (coherente).
    inv = Invoice(
        lines=[_line("L0", 1, "0.1", "0.1"), _line("L1", 1, "0.2", "0.2")],
        subtotal=Obs("sub", Role.SUBTOTAL, to_cents("0.3")),
    )
    r = check_calc_consistency(inv)
    assert r.state is CalcState.COHERENT, r.to_dict()


def test_redondeo_legitimo_dentro_de_tolerancia_no_rechaza():
    # base 100.00 * 21% = 21.00 exacto; metemos una cuota con 1 céntimo de redondeo.
    inv = Invoice(taxes=[_tax(10000, Fraction(21, 100), "2100.01")])
    assert check_calc_consistency(inv, tax_tolerance_cents=1).state is CalcState.COHERENT
    # con ε=0 el mismo caso SÍ se marca (muestra el efecto de la tolerancia)
    assert check_calc_consistency(inv, tax_tolerance_cents=0).state is CalcState.INCONSISTENT


def test_factura_b_total_igual_suma_lineas_sin_iva():
    # Factura B real (CompraGamer 00997224): IVA incluido, sin subtotal ni cuota
    # discriminada → el invariante útil es total = Σlíneas (3.970 + 55.830 = 59.800).
    inv = Invoice(
        lines=[_line("L0", 1, 3970, 3970), _line("L1", 1, 55830, 55830)],
        total=Obs("total", Role.TOTAL, to_cents(59800)),
    )
    r = check_calc_consistency(inv)
    assert r.state is CalcState.COHERENT, r.to_dict()
    # total MENOR que Σlíneas → un impuesto faltante no lo explica → INCONSISTENT
    inv.total.value = to_cents(50000)
    r2 = check_calc_consistency(inv)
    assert r2.state is CalcState.INCONSISTENT
    assert any("Σlíneas" in v.rule for v in r2.violations)
    # total MAYOR que Σlíneas y SIN IVA extraído → podría ser un IVA no extraído →
    # INCOMPLETE, no se inventa que "no hay IVA".
    inv.total.value = to_cents(65000)
    assert check_calc_consistency(inv).state is CalcState.INCOMPLETE


def test_incompleto_sin_datos():
    r = check_calc_consistency(Invoice())
    assert r.state is CalcState.INCOMPLETE
    assert r.invariants_checked == 0


def test_satisface_invariantes_es_limite_no_error():
    # 2*50 y 4*25 dan el mismo amount: internamente consistente -> COHERENT (ciego).
    inv = Invoice(lines=[_line("L0", 4, 25, 100)])
    assert check_calc_consistency(inv).state is CalcState.COHERENT


# ───────────────────────────── integración / regla de oro ──────────────────

def test_attach_calc_bloquea_inconsistencia():
    inv = Invoice(lines=[_line("L0", 3, 40, 150)])
    r = check_calc_consistency(inv)
    out, blocked = attach_calc({"total": 150}, r)
    assert blocked is True
    assert out["total"] == 150            # no destructivo
    assert out["_vera_calc"]["state"] == "inconsistent"


def test_attach_calc_no_bloquea_coherente():
    inv = Invoice(lines=[_line("L0", 3, 40, 120)])
    out, blocked = attach_calc({"ok": 1}, check_calc_consistency(inv))
    assert blocked is False
    assert out["_vera_calc"]["state"] == "coherent"


# ───────────────────────────── corpus (recall/FP global) ───────────────────

def test_corpus_recall_total_y_sin_falsos_positivos():
    cases = build_corpus(per_class=10, seed=3)
    tp = fp = fn = tn = 0
    for c in cases:
        if c.is_faulty and not c.detectable:
            continue  # límite conocido
        pos = mvp_predict(c.invoice, tol=1) is CalcState.INCONSISTENT
        if c.is_faulty:
            tp += pos; fn += not pos
        else:
            fp += pos; tn += not pos
    assert fn == 0, "debería detectar TODOS los fallos detectables"
    assert fp == 0, "no debería marcar clean/redondeo-legítimo"
    assert tp > 0 and tn > 0


def test_corpus_limite_satisface_invariantes_no_se_detecta():
    cases = [c for c in build_corpus(per_class=10, seed=3)
             if c.fault_class == "satisfies_invariants"]
    det = sum(mvp_predict(c.invoice, tol=1) is CalcState.INCONSISTENT for c in cases)
    assert det == 0  # SAT no prueba veracidad: es el límite declarado
    assert len(FAULT_CLASSES) == 13
