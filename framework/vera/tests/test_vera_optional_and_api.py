# Autor: @Raven
"""§1+§2 — `optional` por entidad (ok deja de colapsar con TEXT) + params del API inline.

§2: ok = ninguna entidad INVENTED (alucinación siempre bloquea) ∧ toda entidad REQUERIDA es
SUPPORTED. Una entidad `optional` puede quedar UNVERIFIED/REVIEW sin bloquear ok — hace usable
el flag ok en documentos reales (que siempre traen nombres TEXT → UNVERIFIED).

§1: load_contract (lo que consume el endpoint /vera/verify) lee fmt/currency/locale/page/optional
desde dicts → el EntityIn del router puede declararlos.
"""
from __future__ import annotations

from framework.vera.vera_coverage import load_contract, match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType, serialize_contract
from framework.vera.vera_tribunal import EntityVerdict

NUM, TEXT = SemanticType.NUMBER, SemanticType.TEXT
SUP = EntityVerdict.SUPPORTED


_OCR = "Factura. Cliente: ACME SA. Total a pagar: 1.000,00 EUR."


# ── §2: optional ─────────────────────────────────────────────────────────────
def test_text_optional_no_bloquea_ok() -> None:
    contract = [EntitySpec("total a pagar", NUM),
                EntitySpec("cliente", TEXT, optional=True)]
    out = {"total_a_pagar": "1000.00", "cliente": "ACME SA"}
    rep = match_coverage(out, _OCR, contract)
    assert rep.records[1].verdict is EntityVerdict.UNVERIFIED   # TEXT sigue UNVERIFIED
    assert rep.ok is True                                       # pero NO bloquea ok


def test_text_requerido_si_bloquea_ok() -> None:
    contract = [EntitySpec("total a pagar", NUM), EntitySpec("cliente", TEXT)]   # requerido
    out = {"total_a_pagar": "1000.00", "cliente": "ACME SA"}
    assert match_coverage(out, _OCR, contract).ok is False


def test_optional_invented_si_bloquea_ok() -> None:
    # una alucinación (INVENTED) bloquea ok AUNQUE la entidad sea optional
    contract = [EntitySpec("total a pagar", NUM, optional=True)]
    out = {"total_a_pagar": "9999.00"}     # no está en el OCR → INVENTED
    rep = match_coverage(out, _OCR, contract)
    assert rep.records[0].verdict is EntityVerdict.INVENTED
    assert rep.ok is False


def test_sin_optional_ok_intacto() -> None:
    contract = [EntitySpec("total a pagar", NUM)]
    assert match_coverage({"total_a_pagar": "1000.00"}, _OCR, contract).ok is True


# ── §1: params del contrato vía dicts (lo que arma el EntityIn del router) ────
def test_load_contract_lee_todos_los_params() -> None:
    raw = [
        {"name": "IBAN", "type": "identifier", "fmt": "iban"},
        {"name": "total", "type": "currency_amount", "currency": "€"},
        {"name": "fecha", "type": "date", "locale": "es-AR"},
        {"name": "saldo", "type": "number", "page": 2},
        {"name": "cliente", "type": "text", "optional": True},
    ]
    c = load_contract(raw)
    assert c[0].fmt == "iban"
    assert c[1].currency == "EUR"          # '€' normalizado a ISO
    assert c[2].locale == "es-AR"
    assert c[3].page == 2
    assert c[4].optional is True
    # round-trip: serialize → load conserva los params
    assert load_contract(serialize_contract(c)) == c
