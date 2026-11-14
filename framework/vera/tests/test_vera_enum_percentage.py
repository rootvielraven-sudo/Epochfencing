# Autor: @Raven
"""P2 — ENUM (valor ∈ conjunto ∧ presente) + PERCENTAGE (ancla el número-%).

ENUM: el valor debe ser una de las opciones declaradas EN el contrato Y estar presente en el doc
junto a su etiqueta → SUPPORTED; fuera del conjunto, ausente o en otro rol → REVIEW (nunca
INVENTED). PERCENTAGE: ancla la magnitud del número seguido de '%' (que NUMBER justamente saltea):
21% ≡ 21,0% ≡ 21. Distinto → INVENTED; fuera de su etiqueta → REVIEW.

Filosofía: cero falsos SUPPORTED; lo no confirmable → REVIEW; determinista, sin LLM.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

ENUM, PCT = SemanticType.ENUM, SemanticType.PERCENTAGE
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW

_PAGO = ("contado", "30 dias", "60 dias", "90 dias")


def _e(ocr, value, *, label="condicion de pago", options=_PAGO):
    spec = EntitySpec(label, ENUM, enum=options)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def _p(ocr, value, *, label="alicuota"):
    spec = EntitySpec(label, PCT)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


# ── ENUM ─────────────────────────────────────────────────────────────────────
def test_enum_valido_y_presente_supported() -> None:
    assert _e("Condicion de pago: 30 dias.", "30 dias") is SUP


def test_enum_acentos_insensible() -> None:
    assert _e("Condicion de pago: 30 días.", "30 dias") is SUP


def test_enum_valido_pero_ausente_review() -> None:
    # 'contado' está en el conjunto pero el doc dice otra cosa → no groundeado → REVIEW
    assert _e("Condicion de pago: 30 dias.", "contado") is REV


def test_enum_fuera_del_conjunto_review() -> None:
    assert _e("Condicion de pago: 30 dias.", "120 dias") is REV


def test_enum_otro_rol_review() -> None:
    ocr = "Forma de pago: contado.\nEntrega: 30 dias."   # campos en líneas separadas (real)
    assert _e(ocr, "30 dias", label="forma de pago") is REV


# ── PERCENTAGE ───────────────────────────────────────────────────────────────
def test_pct_con_simbolo_supported() -> None:
    assert _p("Alicuota: 21%.", "21%") is SUP


def test_pct_sin_simbolo_y_decimal_supported() -> None:
    assert _p("Alicuota: 21%.", "21") is SUP
    assert _p("Alicuota: 21,0%.", "21%") is SUP


def test_pct_distinto_invented() -> None:
    assert _p("Alicuota: 21%.", "10%") is INV


def test_pct_otro_rol_review() -> None:
    ocr = "IVA: 21%. Retencion IRPF: 15%."
    assert _p(ocr, "21%", label="retencion irpf") is REV   # 21% no está junto a IRPF


def test_pct_no_numerico_review() -> None:
    assert _p("Alicuota: 21%.", "alto") is REV
