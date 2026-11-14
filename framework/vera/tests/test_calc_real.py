# Autor: @Raven
"""Regresión con FACTURAS REALES (datos-demo), por la ruta de verificación del pipeline.

Números transcriptos de PDFs reales (CompraGamer / Newton Station S.R.L.): una Factura B
simple, una con impuesto interno, y una multi-alícuota (Ley 27.743, IVA 10,5% + 21% con
redondeo real de 1c). Deben verificar COHERENT tal cual; un total adulterado → INCONSISTENT.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_real.py -q
"""
from __future__ import annotations

import copy

import pytest

from framework.vera.calc_consistency import CalcState
from framework.vera.calc_contract import amount_to_cents, to_cents, verify_calc
from framework.vera.calc_eval.inject_real import REAL


@pytest.mark.parametrize("f", REAL, ids=[f["name"] for f in REAL])
def test_factura_real_coherente(f):
    r = verify_calc(f["extraction"], calc_contract=f["contract"])
    assert r.state is CalcState.COHERENT, (f["name"], r.to_dict())
    assert r.invariants_checked >= 3


@pytest.mark.parametrize("f", REAL, ids=[f["name"] for f in REAL])
def test_factura_real_total_adulterado_se_caza(f):
    bad = copy.deepcopy(f["extraction"])
    # total MENOR al real: un impuesto faltante no lo explica → siempre INCONSISTENT
    # (restar, no sumar: un total inflado sin IVA extraído sería INCOMPLETE, no inventa IVA).
    pesos = (amount_to_cents(bad["total"]) - to_cents(100000)) // 100
    bad["total"] = f"$ {int(pesos):,}".replace(",", ".") + ",00"
    r = verify_calc(bad, calc_contract=f["contract"])
    assert r.state is CalcState.INCONSISTENT
    assert any("total" in v.rule for v in r.violations)
