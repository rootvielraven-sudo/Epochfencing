# Autor: @Raven
"""Tests del adaptador de integración MIKA (no portados de Minerva: nuevos del port)."""
from __future__ import annotations

import textwrap

import pytest

from framework.vera import Source, verify
from framework.vera.contract import available_doc_types, load_contract_for
from framework.vera.integration import (
    attach_report,
    map_extraction,
    report_to_legacy,
    verify_extraction,
    vera_engine_enabled,
    vera_mode,
)

_YAML = textwrap.dedent("""
    factura:
      entities:
        - {name: "Total", type: number, aliases: ["Total a pagar"]}
        - {name: "Fecha", type: date, locale: es-AR}
        - {name: "Comprobante", type: identifier}
""")


@pytest.fixture()
def cfg_root(tmp_path):
    d = tmp_path / "default"
    d.mkdir()
    (d / "vera_contracts.yml").write_text(_YAML, encoding="utf-8")
    load_contract_for.cache_clear() if hasattr(load_contract_for, "cache_clear") else None
    return str(tmp_path)


# ── Loader de contratos ───────────────────────────────────────────────────────

def test_load_contract_for_reads_config(cfg_root):
    c = load_contract_for("factura", config_root=cfg_root)
    assert c is not None
    assert [e.name for e in c] == ["Total", "Fecha", "Comprobante"]
    assert c[0].type.value == "number"


def test_load_contract_for_unknown_doctype_is_none(cfg_root):
    assert load_contract_for("no_existe", config_root=cfg_root) is None


def test_load_contract_for_none_doctype_is_none(cfg_root):
    assert load_contract_for(None, config_root=cfg_root) is None


def test_available_doc_types(cfg_root):
    assert available_doc_types(config_root=cfg_root) == ["factura"]


# ── Mapeo output→slug (dict anidado del LLM) ──────────────────────────────────

def test_map_extraction_from_nested_output(cfg_root):
    contract = load_contract_for("factura", config_root=cfg_root)
    # Output crudo del LLM: anidado y con NOMBRES humanos / alias, no slugs.
    nested = {"cabecera": {"Total a pagar": "3.025.000,00", "Fecha": "15/03/2024"},
              "items": [{"Comprobante": "A-0001-00012345"}]}
    slugged = map_extraction(nested, contract)
    assert slugged["Total"] == "3.025.000,00"     # por alias "Total a pagar"
    assert slugged["Fecha"] == "15/03/2024"
    assert slugged["Comprobante"] == "A-0001-00012345"


# ── Flag / gating ─────────────────────────────────────────────────────────────

def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("VERA_ENGINE", raising=False)
    assert vera_engine_enabled() is False


def test_flag_on_with_env(monkeypatch):
    monkeypatch.setenv("VERA_ENGINE", "minerva")
    assert vera_engine_enabled() is True


def test_verify_extraction_inert_when_flag_off(monkeypatch):
    monkeypatch.delenv("VERA_ENGINE", raising=False)
    # sin contrato explícito y flag off → None (caller usa legacy)
    assert verify_extraction({"Total": "100"}, "Total: 100", doc_type="factura") is None


def test_verify_extraction_runs_with_explicit_contract(cfg_root):
    contract = load_contract_for("factura", config_root=cfg_root)
    ocr = "FACTURA B\nTotal: 3.025.000,00\nFecha: 15/03/2024\nComprobante: A-0001-00012345\n"
    out = {"Total": "3025000.00", "Fecha": "2024-03-15", "Comprobante": "A-0001-00012345"}
    rep = verify_extraction(out, ocr, contract=contract)
    assert rep is not None and rep.ok
    assert rep.counts()["supported"] == 3


# ── Mapeo a formato legacy ────────────────────────────────────────────────────

def test_report_to_legacy_ok(cfg_root):
    contract = load_contract_for("factura", config_root=cfg_root)
    ocr = "Total: 100,00\nFecha: 01/01/2024\nComprobante: A-0001-00000001\n"
    out = {"Total": "100.00", "Fecha": "2024-01-01", "Comprobante": "A-0001-00000001"}
    rep = verify(out, Source.ocr(ocr), contract)
    assert report_to_legacy(rep) == "OK"


def test_report_to_legacy_flags_hallucination(cfg_root):
    contract = load_contract_for("factura", config_root=cfg_root)
    ocr = "Total: 100,00\nFecha: 01/01/2024\nComprobante: A-0001-00000001\n"
    out = {"Total": "999999,99", "Fecha": "2024-01-01", "Comprobante": "A-0001-00000001"}
    rep = verify(out, Source.ocr(ocr), contract)
    legacy = report_to_legacy(rep)
    assert isinstance(legacy, dict)
    assert legacy["ok"] is False
    assert any(f["ruta"] == "Total" for f in legacy["valores_llm_no_en_ocr"])


# ── Modo autoritativo / attach ────────────────────────────────────────────────

def test_vera_mode_default_shadow(monkeypatch):
    monkeypatch.delenv("VERA_MODE", raising=False)
    assert vera_mode() == "shadow"


def test_vera_mode_authoritative(monkeypatch):
    monkeypatch.setenv("VERA_MODE", "authoritative")
    assert vera_mode() == "authoritative"


def test_attach_report_is_non_destructive(cfg_root):
    contract = load_contract_for("factura", config_root=cfg_root)
    ocr = "Total: 100,00\nFecha: 01/01/2024\nComprobante: A-0001-00000001\n"
    out = {"Total": "999999,99", "Fecha": "2024-01-01", "Comprobante": "A-0001-00000001"}
    rep = verify(out, Source.ocr(ocr), contract)
    result = {"facturas": [{"x": 1}], "state": "Verified"}   # output legacy simulado
    new_result, blocked = attach_report(result, rep)
    assert blocked is True                          # Total=999999,99 → INVENTED
    assert new_result["facturas"] == [{"x": 1}]     # campos originales intactos
    assert new_result["state"] == "Verified"
    assert new_result["_vera"]["ok"] is False
    assert any(f["campo"] == "Total" for f in new_result["_vera"]["findings"])
