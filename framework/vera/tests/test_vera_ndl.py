# Autor: @Raven
"""NDL — memoria diacrónica de anti-patrones (capa 4 del paper)."""
from __future__ import annotations

from framework.vera import Source, load_contract, verify
from framework.vera.ndl import NDL, apply_ndl

_CONTRACT = load_contract({"entities": [{"name": "Total", "type": "number"}]})


def test_jaccard_threshold_no_dispara_con_contexto_distinto():
    ndl = NDL()
    ndl.record("factura", "Total", "acme sa marzo 2024 servicios", "1000")
    sig = ndl.check("factura", "Total", "otra empresa distinta enero pagos", "1000")
    assert sig.matched is False


def test_context_hit_pero_valor_distinto():
    ndl = NDL()
    ndl.record("factura", "Total", "acme sa marzo 2024 servicios", "1000")
    sig = ndl.check("factura", "Total", "acme sa marzo 2024 servicios varios", "2500")
    assert sig.matched is True and sig.value_hit is False


def test_value_hit_en_contexto_similar():
    ndl = NDL()
    ndl.record("factura", "Total", "acme sa marzo 2024 servicios", "1000")
    sig = ndl.check("factura", "Total", "acme sa marzo 2024 servicios detalle", "1000")
    assert sig.matched is True and sig.value_hit is True and sig.count == 1


def test_record_refuerza_count():
    ndl = NDL()
    ctx = "acme sa marzo 2024 servicios"
    ndl.record("factura", "Total", ctx, "1000")
    ndl.record("factura", "Total", ctx, "1000")
    assert ndl.check("factura", "Total", ctx, "1000").count == 2


def test_apply_ndl_refuerza_supported_a_review():
    ndl = NDL()
    ctx = "acme sa marzo 2024 servicios"
    # 1ª inferencia: el plano acepta 1.000,00 (aparece pegado a su etiqueta) → SUPPORTED,
    #    pero luego se supo que era una alucinación; la registramos como anti-patrón.
    ndl.record("factura", "Total", ctx, "1000")
    # 2ª inferencia: mismo contexto, el LLM vuelve a producir 1.000,00 y el plano lo da SUPPORTED.
    rep = verify({"Total": "1000"}, Source.ocr("Total: 1.000,00\n"), _CONTRACT)
    assert rep.records[0].verdict.value == "supported"
    rep2 = apply_ndl(rep, ndl, "factura", ctx, contract=_CONTRACT)
    assert rep2.records[0].verdict.value == "review"       # NDL reforzó
    assert "NDL" in rep2.records[0].reason


def test_apply_ndl_aprende_de_invented():
    ndl = NDL()
    ctx = "acme sa marzo 2024"
    rep = verify({"Total": "9999"}, Source.ocr("Total: 100,00\n"), _CONTRACT)  # 9999 → INVENTED
    assert rep.records[0].verdict.value == "invented"
    apply_ndl(rep, ndl, "factura", ctx, contract=_CONTRACT)
    # tras aprender, un futuro 9999 en contexto similar dispara value_hit
    assert ndl.check("factura", "Total", ctx + " extra", "9999").value_hit is True
