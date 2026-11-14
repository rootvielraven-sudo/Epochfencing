# Autor: @Raven
"""VERA punto 2 — matcher de cobertura: SUPPORTED/INVENTED/UNVERIFIED por entidad,
anclaje canónico (no literal), carga de contrato desde config. ADR-022."""
from __future__ import annotations

import pytest
from framework.vera.vera_coverage import load_contract, match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

_OCR = ("FACTURA. Base imponible: 1.234,56 €. IVA (21%): 259,26 €. "
        "Total a pagar: 1.493,82 €.")
_CONTRACT = [
    EntitySpec("base imponible", SemanticType.NUMBER),
    EntitySpec("iva", SemanticType.NUMBER),
    EntitySpec("total a pagar", SemanticType.NUMBER),
]


def _verdict(out: dict, name: str) -> EntityVerdict:
    rep = match_coverage(out, _OCR, _CONTRACT)
    return next(r.verdict for r in rep.records if r.entity_name == name)


# ── los cuatro casos por entidad ─────────────────────────────────────────────
def test_supported_literal() -> None:
    out = {"base_imponible": "1.234,56", "iva": "259,26", "total_a_pagar": "1.493,82"}
    assert _verdict(out, "total a pagar") is EntityVerdict.SUPPORTED


def test_supported_reformateado_ancla() -> None:
    # mismo valor, OTRO formato (plano EN vs ES del OCR) → DEBE anclar (cazador de FP)
    out = {"base_imponible": "1234.56", "iva": "259.26", "total_a_pagar": "1493.82"}
    rep = match_coverage(out, _OCR, _CONTRACT)
    assert all(r.verdict is EntityVerdict.SUPPORTED for r in rep.records)
    assert rep.ok


def test_invented() -> None:
    out = {"base_imponible": "1.234,56", "iva": "259,26", "total_a_pagar": "9.999,99"}
    assert _verdict(out, "total a pagar") is EntityVerdict.INVENTED


def test_unverified_null() -> None:
    out = {"base_imponible": "1.234,56", "iva": "259,26", "total_a_pagar": None}
    assert _verdict(out, "total a pagar") is EntityVerdict.UNVERIFIED


# ── agregado: OK solo si todas SUPPORTED ─────────────────────────────────────
def test_ok_global_solo_si_todas_supported() -> None:
    ok_out = {"base_imponible": "1.234,56", "iva": "259,26", "total_a_pagar": "1.493,82"}
    assert match_coverage(ok_out, _OCR, _CONTRACT).ok
    bad = {"base_imponible": "1.234,56", "iva": None, "total_a_pagar": "9.999,99"}
    rep = match_coverage(bad, _OCR, _CONTRACT)
    assert not rep.ok
    # detalle por entidad en notify (cuál y por qué)
    nombres = {r.entity_name for r in rep.notify}
    assert nombres == {"iva", "total a pagar"}


def test_counts() -> None:
    bad = {"base_imponible": "1.234,56", "iva": None, "total_a_pagar": "9.999,99"}
    c = match_coverage(bad, _OCR, _CONTRACT).counts()
    assert c == {"supported": 1, "invented": 1, "unverified": 1}


# ── carga desde config ───────────────────────────────────────────────────────
def test_load_contract_lista_y_dict() -> None:
    c1 = load_contract([{"name": "total", "type": "number"}])
    c2 = load_contract({"level": "fuerte", "entities": [{"name": "total", "type": "number"}]})
    assert c1 == c2 == [EntitySpec("total", SemanticType.NUMBER)]


def test_load_contract_tipo_invalido_falla() -> None:
    with pytest.raises(ValueError):
        load_contract([{"name": "x", "type": "no_existe"}])
    with pytest.raises(ValueError):
        load_contract([{"name": "x"}])   # falta type


def test_load_contract_slug_colisionante_falla() -> None:
    # dos nombres que slugifican igual ("Total a pagar (€)" vs "Total a pagar $") → ValueError
    with pytest.raises(ValueError, match="colision"):
        load_contract([{"name": "Total a pagar (€)", "type": "number"},
                       {"name": "Total a pagar $", "type": "number"}])
