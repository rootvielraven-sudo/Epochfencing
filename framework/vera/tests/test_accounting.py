# Autor: @Raven
"""Tests de la consistencia de cálculo CONTABLE: partida doble y ecuación patrimonial.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_accounting.py -q
"""
from __future__ import annotations

from framework.vera.accounting import (
    AccountEntry, AccountingDoc, Balance, Journal, Statement, check_accounting,
)
from framework.vera.calc_consistency import CalcState, Obs, Role, to_cents
from framework.vera.calc_diagnosis import diagnose


def _d(aid, v):
    return Obs(aid, Role.DEBIT, to_cents(v))


def _h(aid, v):
    return Obs(aid, Role.CREDIT, to_cents(v))


def _bal(a, p, pn):
    return Balance(assets=Obs("a", Role.ASSETS, to_cents(a)),
                   liabilities=Obs("p", Role.LIABILITIES, to_cents(p)),
                   equity=Obs("pn", Role.EQUITY, to_cents(pn)))


# ───────────────────────────── partida doble ───────────────────────────────

def test_asiento_balanceado():
    j = Journal([AccountEntry("Caja", debit=_d("d0", 1000)),
                 AccountEntry("Banco", debit=_d("d1", 500)),
                 AccountEntry("Ventas", credit=_h("h0", 1500))])
    assert check_accounting(AccountingDoc(journal=j)).state is CalcState.COHERENT


def test_asiento_desbalanceado_se_caza():
    j = Journal([AccountEntry("Caja", debit=_d("d0", 1000)),
                 AccountEntry("Ventas", credit=_h("h0", 1400))])
    r = check_accounting(AccountingDoc(journal=j))
    assert r.state is CalcState.INCONSISTENT
    assert any(v.kind == "debe_haber" for v in r.violations)
    # el diagnóstico ofrece balancear una suma con la otra
    sugeridos = {rep["suggested"] for c in diagnose(r)["conflicts"] for rep in c["repairs"]}
    assert "1.000,00" in sugeridos  # Σhaber debería igualar al debe (1000)


def test_asiento_incompleto_sin_haber():
    j = Journal([AccountEntry("Caja", debit=_d("d0", 1000))])  # solo debe
    r = check_accounting(AccountingDoc(journal=j))
    assert r.state is CalcState.INCOMPLETE


def test_asiento_tolerancia_redondeo():
    # 1 céntimo de desvío por redondeo, dentro de tolerancia
    j = Journal([AccountEntry("Caja", debit=_d("d0", 1000)),
                 AccountEntry("Ventas", credit=Obs("h0", Role.CREDIT, to_cents("1000.01")))])
    assert check_accounting(AccountingDoc(journal=j)).state is CalcState.COHERENT


# ───────────────────────────── ecuación patrimonial ────────────────────────

def test_balance_coherente():
    assert check_accounting(AccountingDoc(balance=_bal(10000, 4000, 6000))).state is CalcState.COHERENT


def test_balance_no_cuadra_y_diagnostica():
    r = check_accounting(AccountingDoc(balance=_bal(10000, 4000, 5500)))
    assert r.state is CalcState.INCONSISTENT
    assert any(v.kind == "balance_equation" for v in r.violations)
    reps = {rep["role"]: rep for c in diagnose(r)["conflicts"] for rep in c["repairs"]}
    # PN debería ser activo - pasivo = 6000
    assert reps["equity"]["suggested"] == "6.000,00"
    assert reps["assets"]["suggested"] == "9.500,00"  # o el activo bajar a 9.500


def test_balance_incompleto_solo_activo():
    b = Balance(assets=Obs("a", Role.ASSETS, to_cents(10000)))  # sin pasivo ni PN
    assert check_accounting(AccountingDoc(balance=b)).state is CalcState.INCOMPLETE


def test_asiento_y_balance_juntos():
    j = Journal([AccountEntry("Caja", debit=_d("d0", 1000)),
                 AccountEntry("Ventas", credit=_h("h0", 1000))])
    doc = AccountingDoc(journal=j, balance=_bal(10000, 4000, 6000))
    r = check_accounting(doc)
    assert r.state is CalcState.COHERENT
    assert r.invariants_checked == 2


def test_sin_datos_contables_incompleto():
    assert check_accounting(AccountingDoc()).state is CalcState.INCOMPLETE


# ───────────────────────────── autodetección end-to-end ────────────────────

def test_autodetecta_asiento_desde_extraccion():
    from framework.vera.calc_contract import verify_calc
    ok = {"asientos": [{"cuenta": "Caja", "debe": "$1.000,00"},
                       {"cuenta": "Banco", "debe": "$500,00"},
                       {"cuenta": "Ventas", "haber": "$1.500,00"}]}
    assert verify_calc(ok).state is CalcState.COHERENT
    mal = {"asientos": [{"cuenta": "Caja", "debe": "$1.000,00"},
                        {"cuenta": "Ventas", "haber": "$1.400,00"}]}
    assert verify_calc(mal).state is CalcState.INCONSISTENT


def test_autodetecta_balance_desde_extraccion():
    from framework.vera.calc_contract import verify_calc
    ok = {"activo": "$10.000,00", "pasivo": "$4.000,00", "patrimonio_neto": "$6.000,00"}
    assert verify_calc(ok).state is CalcState.COHERENT
    mal = {"activo": "$10.000,00", "pasivo": "$4.000,00", "patrimonio_neto": "$5.500,00"}
    assert verify_calc(mal).state is CalcState.INCONSISTENT


# ───────────────────────────── resumen de cuenta / mayor ───────────────────

def _c(cid, v):
    return Obs(cid, Role.CREDIT, to_cents(v))


def test_resumen_de_cuenta_coherente():
    s = Statement(opening=Obs("op", Role.SUBTOTAL, to_cents(10000)),
                  credits=[_c("c0", 5000)], debits=[_d("b0", 2000)],
                  closing=Obs("cl", Role.TOTAL, to_cents(13000)))
    assert check_accounting(AccountingDoc(statement=s)).state is CalcState.COHERENT


def test_resumen_saldo_final_mal():
    s = Statement(opening=Obs("op", Role.SUBTOTAL, to_cents(10000)),
                  credits=[_c("c0", 5000)], debits=[_d("b0", 2000)],
                  closing=Obs("cl", Role.TOTAL, to_cents(15000)))
    r = check_accounting(AccountingDoc(statement=s))
    assert r.state is CalcState.INCONSISTENT
    assert any(v.kind == "statement_balance" for v in r.violations)
    reps = {rep["suggested"] for c in diagnose(r)["conflicts"] for rep in c["repairs"]}
    assert "13.000,00" in reps  # el saldo final debería ser 13.000


def test_resumen_sin_movimientos_es_incompleto():
    # saldo inicial != final pero sin movimientos extraídos → no se verifica (no inventa)
    s = Statement(opening=Obs("op", Role.SUBTOTAL, to_cents(10000)),
                  closing=Obs("cl", Role.TOTAL, to_cents(13000)))
    assert check_accounting(AccountingDoc(statement=s)).state is CalcState.INCOMPLETE


def test_autodetecta_resumen_desde_extraccion():
    from framework.vera.calc_contract import verify_calc
    e = {"saldo_inicial": "$10.000,00", "saldo_final": "$13.000,00",
         "movimientos": [{"credito": "$5.000,00"}, {"debito": "$2.000,00"}]}
    assert verify_calc(e).state is CalcState.COHERENT
    assert verify_calc(dict(e, saldo_final="$99.000,00")).state is CalcState.INCONSISTENT


def test_factura_no_se_rutea_a_contable():
    # una factura con líneas/total no debe tratarse como asiento/balance
    from framework.vera.calc_contract import infer_accounting, verify_calc
    factura = {"items": [{"cantidad": "2", "precio_unitario": "$100,00", "importe": "$200,00"}],
               "total": "$200,00"}
    assert infer_accounting(factura) is None
    assert verify_calc(factura).state is CalcState.COHERENT
