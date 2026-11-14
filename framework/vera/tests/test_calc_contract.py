# Autor: @Raven
"""Tests del role-tagging: extracción (header + líneas, strings es-AR) → Invoice →
veredicto de consistencia de cálculo. Incluye carga del bloque ``calc`` del contrato.

Ejecutar:
    ./.venv/bin/python -m pytest framework/vera/tests/test_calc_contract.py -q
"""
from __future__ import annotations

from fractions import Fraction

from framework.vera.calc_consistency import CalcState, attach_calc
from framework.vera.calc_contract import (
    CalcContract, CalcLineMap, CalcTaxMap, amount_to_cents, calc_enabled,
    calc_mode, infer_calc_contract, invoice_from_extraction, load_calc_contract,
    rate_to_fraction, verify_calc,
)


# ───────────────────────────── parseo exacto ───────────────────────────────

def test_amount_to_cents_es_ar_exacto():
    assert amount_to_cents("$ 1.234,56") == 123456
    assert amount_to_cents("1234.56") == 123456
    assert amount_to_cents("2.000,00") == 200000
    assert amount_to_cents("") is None
    # exactitud: 0.1 + 0.2 en céntimos es exacto (Decimal->Fraction, sin float)
    assert amount_to_cents("0,10") + amount_to_cents("0,20") == amount_to_cents("0,30")


def test_rate_to_fraction():
    assert rate_to_fraction("21%") == Fraction(21, 100)
    assert rate_to_fraction("21") == Fraction(21, 100)
    assert rate_to_fraction("0,21") == Fraction(21, 100)
    assert rate_to_fraction(None) is None


# ───────────────────────────── extracción realista ─────────────────────────

_CC = CalcContract(
    lines=CalcLineMap(array="items", qty="cantidad", price="precio_unit", amount="importe"),
    subtotal="subtotal",
    total="total",
    taxes=(CalcTaxMap(base="base_imponible", rate="alicuota_iva", amount="iva", tax_id="iva"),),
)


def _extraction():
    return {
        "items": [
            {"cantidad": "2", "precio_unit": "$ 1.000,00", "importe": "$ 2.000,00"},
            {"cantidad": "3", "precio_unit": "$ 500,00", "importe": "$ 1.500,00"},
        ],
        "subtotal": "$ 3.500,00",
        "base_imponible": "$ 3.500,00",
        "alicuota_iva": "21%",
        "iva": "$ 735,00",
        "total": "$ 4.235,00",
    }


def test_extraccion_coherente():
    r = verify_calc(_extraction(), calc_contract=_CC)
    assert r is not None
    assert r.state is CalcState.COHERENT, r.to_dict()
    assert r.invariants_checked >= 4  # 2 líneas + subtotal + iva + total


def test_extraccion_total_mal_bloquea():
    e = _extraction()
    e["total"] = "$ 9.999,00"   # total copiado/erróneo
    r = verify_calc(e, calc_contract=_CC)
    assert r.state is CalcState.INCONSISTENT
    assert any("total" in v.rule for v in r.violations)


def test_extraccion_importe_de_linea_mal():
    e = _extraction()
    e["items"][0]["importe"] = "$ 2.500,00"  # 2*1000 != 2500
    r = verify_calc(e, calc_contract=_CC)
    assert r.state is CalcState.INCONSISTENT
    assert any("line[0]" in v.rule for v in r.violations)


def test_valor_no_parseable_no_inventa_inconsistencia():
    # importe ilegible → esa obs se omite; NO debe marcar inconsistencia falsa.
    e = _extraction()
    e["items"][0]["importe"] = "ilegible"
    r = verify_calc(e, calc_contract=_CC)
    # la línea 0 queda sin importe (incompleta), pero el resto cierra → no INCONSISTENT
    assert r.state is not CalcState.INCONSISTENT


def test_provenance_conserva_el_crudo():
    inv = invoice_from_extraction(_extraction(), _CC)
    assert inv.total.provenance["raw"] == "$ 4.235,00"
    assert inv.lines[0].amount.text == "$ 2.000,00"


# ───────────────────────────── carga del contrato ──────────────────────────

_YAML = """
factura:
  entities:
    - {name: "Total", type: currency_amount, currency: ARS}
  calc:
    lines: {array: "items", qty: "cantidad", price: "precio_unit", amount: "importe"}
    subtotal: "subtotal"
    total: "total"
    taxes:
      - {base: "base_imponible", rate: "alicuota_iva", amount: "iva", id: "iva"}
recibo:
  entities:
    - {name: "Fecha", type: date}
"""


def test_load_calc_contract_desde_yaml(tmp_path):
    d = tmp_path / "default"
    d.mkdir()
    (d / "vera_contracts.yml").write_text(_YAML, encoding="utf-8")

    cc = load_calc_contract("factura", config_root=str(tmp_path))
    assert cc is not None
    assert cc.lines.array == "items" and cc.lines.qty == "cantidad"
    assert cc.total == "total"
    assert cc.taxes[0].base == "base_imponible" and cc.taxes[0].tax_id == "iva"

    # end-to-end con el contrato cargado del fichero
    r = verify_calc(_extraction(), doc_type="factura", config_root=str(tmp_path))
    assert r.state is CalcState.COHERENT


# ───────────────────────────── auto-detección (sin config) ─────────────────

def test_autodetect_factura_simple():
    e = {"items": [{"cantidad": "2", "precio_unitario": "$1.000,00", "importe": "$2.000,00"}],
         "subtotal": "$2.000,00", "total": "$2.000,00"}
    r = verify_calc(e)  # sin doc_type ni contrato → infiere
    assert r is not None and r.state is CalcState.COHERENT


def test_autodetect_claves_alternativas_con_iva():
    e = {"detalle": [{"cant": "3", "precio": "100,00", "monto": "300,00"}],
         "base_imponible": "300,00", "iva": "63,00", "importe_total": "363,00"}
    cc = infer_calc_contract(e)
    assert cc is not None
    assert cc.total == "importe_total" and cc.lines.array == "detalle"
    assert cc.lines.amount == "monto" and cc.taxes[0].amount == "iva"
    assert verify_calc(e).state is CalcState.COHERENT


def test_autodetect_total_menor_inconsistente():
    # total MENOR que Σlíneas → no lo explica un IVA faltante → INCONSISTENT
    e = {"items": [{"cantidad": "2", "precio_unitario": "100,00", "importe": "200,00"}],
         "total": "150,00"}
    assert verify_calc(e).state is CalcState.INCONSISTENT


def test_autodetect_total_mayor_sin_iva_es_incompleto():
    # total MAYOR que Σlíneas y sin IVA extraído → posible IVA no extraído → INCOMPLETE
    e = {"items": [{"cantidad": "2", "precio_unitario": "100,00", "importe": "200,00"}],
         "total": "242,00"}
    assert verify_calc(e).state is CalcState.INCOMPLETE


def test_autodetect_libro_iva_chequea_base_por_tasa():
    # neto gravado × alícuota = IVA, y neto + IVA = total
    e = {"neto_gravado": "$10.000,00", "alicuota": "21%", "iva": "$2.100,00",
         "total": "$12.100,00"}
    assert verify_calc(e).state is CalcState.COHERENT
    # IVA mal (no es el 21% del neto) → inconsistente por base×tasa
    mal = {"neto_gravado": "$10.000,00", "alicuota": "21%", "iva": "$3.000,00",
           "total": "$13.000,00"}
    r = verify_calc(mal)
    assert r.state is CalcState.INCONSISTENT
    assert any("tax[iva]" in v.rule for v in r.violations)


def test_autodetect_no_factura_devuelve_none():
    # receta médica: sin campos de cuentas → no se infiere contrato → None (sin badge)
    e = {"paciente": "Juan", "medicamento": "X", "dosis": "500mg", "matricula": "12345"}
    assert infer_calc_contract(e) is None
    assert verify_calc(e) is None


def test_autodetect_desactivable():
    e = {"items": [{"cantidad": "2", "precio_unitario": "100,00", "importe": "200,00"}], "total": "200,00"}
    assert verify_calc(e, autodetect=False) is None   # sin inferencia → None


# ───────────────────────────── flags opt-in (pipeline) ─────────────────────

def test_flags_default_off_shadow(monkeypatch):
    monkeypatch.delenv("VERA_CALC", raising=False)
    monkeypatch.delenv("VERA_CALC_MODE", raising=False)
    assert calc_enabled() is False      # inerte por default
    assert calc_mode() == "shadow"


def test_flags_on_authoritative(monkeypatch):
    monkeypatch.setenv("VERA_CALC", "on")
    monkeypatch.setenv("VERA_CALC_MODE", "authoritative")
    assert calc_enabled() is True
    assert calc_mode() == "authoritative"


def test_forma_pipeline_autoritativo_degrada_estado():
    # Replica lo que hace process_api en modo autoritativo: attach + enforce.
    e = _extraction()
    e["total"] = "$ 9.999,00"
    rep = verify_calc(e, calc_contract=_CC)
    out = {"state": "Verified"}  # como si la verificación legacy hubiera dado OK
    out, blocked = attach_calc(out, rep)
    out["vera_calc_state"] = "blocked" if blocked else "clean"
    if out.get("vera_calc_state") == "blocked":
        out["state"] = "Not_verified"
    assert blocked is True
    assert out["state"] == "Not_verified"        # la regla de oro se cumple
    assert out["_vera_calc"]["state"] == "inconsistent"


def test_sin_bloque_calc_es_inerte(tmp_path):
    d = tmp_path / "default"
    d.mkdir()
    (d / "vera_contracts.yml").write_text(_YAML, encoding="utf-8")
    # 'recibo' no tiene bloque calc → None (sin inferencia, dimensión inerte por config)
    assert load_calc_contract("recibo", config_root=str(tmp_path)) is None
    assert verify_calc(_extraction(), doc_type="recibo", config_root=str(tmp_path), autodetect=False) is None
    # doc_type inexistente → None
    assert verify_calc(_extraction(), doc_type="nope", config_root=str(tmp_path), autodetect=False) is None
