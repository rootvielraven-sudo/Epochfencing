# Autor: @Raven
"""Tests del soporte de DEDUCCIONES (nómina): base*tasa=monto por concepto + el monto
RESTA del bruto (remunerativo - Σdeducciones = neto). Incluye carga del bloque `calc`
de `nomina` desde el contrato y diagnóstico de un concepto mal.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_deductions.py -q
"""
from __future__ import annotations

from fractions import Fraction

from framework.vera.calc_consistency import (
    CalcState, Deduction, Invoice, Obs, Role, check_calc_consistency,
    to_cents, to_fraction,
)
from framework.vera.calc_contract import (
    CalcContract, CalcDeductionMap, load_calc_contract, verify_calc,
)
from framework.vera.calc_diagnosis import diagnose


def _ded(did, base, rate, amount):
    return Deduction(did,
                     Obs(f"{did}.b", Role.TAX_BASE, to_cents(base)),
                     Obs(f"{did}.r", Role.TAX_RATE, to_fraction(rate)),
                     Obs(f"{did}.a", Role.TAX_AMOUNT, to_cents(amount)))


def test_neto_igual_bruto_menos_deducciones():
    inv = Invoice(
        subtotal=Obs("bruto", Role.SUBTOTAL, to_cents(1_000_000)),
        deductions=[_ded("jub", 1_000_000, Fraction(11, 100), 110_000),
                    _ded("os", 1_000_000, Fraction(3, 100), 30_000)],
        total=Obs("neto", Role.TOTAL, to_cents(860_000)))  # 1.000.000 - 140.000
    r = check_calc_consistency(inv)
    assert r.state is CalcState.COHERENT, r.to_dict()
    assert r.invariants_checked == 3   # 2 deducciones + total


def test_concepto_mal_calculado_se_caza():
    # jubilación: 11% de 1.000.000 = 110.000, no 150.000
    inv = Invoice(
        subtotal=Obs("bruto", Role.SUBTOTAL, to_cents(1_000_000)),
        deductions=[_ded("jub", 1_000_000, Fraction(11, 100), 150_000)],
        total=Obs("neto", Role.TOTAL, to_cents(850_000)))
    r = check_calc_consistency(inv)
    assert r.state is CalcState.INCONSISTENT
    assert any("deduction[jub]" in v.rule for v in r.violations)
    rep = next(x for c in diagnose(r)["conflicts"] for x in c["repairs"]
               if x["role"] == "amount")
    assert rep["suggested"] == "110.000,00"   # el valor correcto de la jubilación


def test_neto_no_cierra_con_deducciones():
    # conceptos OK pero el neto impreso está mal → total dispara, culpable = neto
    inv = Invoice(
        subtotal=Obs("bruto", Role.SUBTOTAL, to_cents(1_000_000)),
        deductions=[_ded("jub", 1_000_000, Fraction(11, 100), 110_000)],
        total=Obs("neto", Role.TOTAL, to_cents(999_999)))  # debería ser 890.000
    r = check_calc_consistency(inv)
    assert r.state is CalcState.INCONSISTENT
    assert any(v.kind == "total_sum" for v in r.violations)


def test_deduccion_monto_fijo_sin_tasa_solo_resta():
    # sin tasa: no se chequea base*tasa, pero su monto baja el neto
    inv = Invoice(
        subtotal=Obs("bruto", Role.SUBTOTAL, to_cents(500_000)),
        deductions=[Deduction("sindicato", amount=Obs("s.a", Role.TAX_AMOUNT, to_cents(10_000)))],
        total=Obs("neto", Role.TOTAL, to_cents(490_000)))
    assert check_calc_consistency(inv).state is CalcState.COHERENT


# ───────────────────────────── role-tagging + contrato ─────────────────────

_CC = CalcContract(
    subtotal="remunerativo", total="neto",
    deductions=(
        CalcDeductionMap(base="remunerativo", rate="alic_jub", amount="jubilacion", ded_id="jubilacion"),
        CalcDeductionMap(base="remunerativo", rate="alic_os", amount="obra_social", ded_id="obra_social"),
    ))


def test_verify_calc_nomina_desde_extraccion():
    e = {"remunerativo": "$1.000.000,00",
         "alic_jub": "11%", "jubilacion": "$110.000,00",
         "alic_os": "3%", "obra_social": "$30.000,00",
         "neto": "$860.000,00"}
    assert verify_calc(e, calc_contract=_CC).state is CalcState.COHERENT
    bad = dict(e, neto="$900.000,00")
    assert verify_calc(bad, calc_contract=_CC).state is CalcState.INCONSISTENT


def test_carga_bloque_calc_nomina_del_contrato():
    cc = load_calc_contract("nomina", config_root="config")
    assert cc is not None
    assert cc.subtotal == "remunerativo" and cc.total == "neto"
    ids = {d.ded_id for d in cc.deductions}
    assert {"jubilacion", "ley_19032", "obra_social"} <= ids
