# Autor: @Raven
"""P1 — CURRENCY_AMOUNT: ancla magnitud Y divisa (cierra el falso-SUPPORTED de moneda CASO 2).

NUMBER ancla solo magnitud (€100 ≡ $100 → SUPPORTED). Un campo CURRENCY_AMOUNT con divisa
ESPERADA declarada en el contrato exige que la divisa del documento junto al importe coincida:
distinta → REVIEW; no confirmable → REVIEW; igual → SUPPORTED. Sin divisa esperada → como NUMBER.

Filosofía regulatoria: cero falsos SUPPORTED; lo no confirmable → REVIEW; sin LLM; determinista.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

CUR = SemanticType.CURRENCY_AMOUNT
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


def _v(ocr: str, value: str, *, label: str = "total a pagar", currency: str | None = None) -> EntityVerdict:
    spec = EntitySpec(label, CUR, currency=currency)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


# ── divisa esperada declarada ────────────────────────────────────────────────
def test_divisa_esperada_coincide_supported() -> None:
    assert _v("Total a pagar: 1.000,00 EUR.", "1000.00", currency="EUR") is SUP
    assert _v("Total a pagar: €1.000,00.", "1000.00", currency="EUR") is SUP   # símbolo ≡ ISO


def test_divisa_del_documento_distinta_es_review() -> None:
    # CASO 2 que se cerraba: doc en USD, campo espera EUR, LLM da el número pelado → REVIEW
    assert _v("Total a pagar: 1.000,00 USD.", "1000.00", currency="EUR") is REV


def test_sin_divisa_confirmable_es_review() -> None:
    # campo espera EUR pero el importe del doc no trae divisa → no se confirma → REVIEW
    assert _v("Total a pagar: 1.000,00.", "1000.00", currency="EUR") is REV


def test_llm_declara_divisa_contradicha_es_invented() -> None:
    # el LLM afirma USD pero el OCR muestra EUR para ese importe → alucinación de divisa
    assert _v("Total a pagar: 1.000,00 EUR.", "1000.00 USD", currency="EUR") is INV


# ── sin divisa esperada → como NUMBER (magnitud) ─────────────────────────────
def test_sin_divisa_esperada_se_comporta_como_number() -> None:
    assert _v("Total a pagar: 1.000,00 USD.", "1000.00") is SUP   # magnitud ancla, divisa libre


def test_magnitud_inventada_es_invented() -> None:
    assert _v("Total a pagar: 1.000,00 EUR.", "9999.00", currency="EUR") is INV
