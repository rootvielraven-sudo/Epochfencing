# Autor: @Raven
"""VERA — traza visible (ADR-022 paso 5): cada veredicto trae el fragmento del OCR."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

_OCR = ("FACTURA. CUIT del emisor: 30-71234567-8. Base imponible: 5.000.000,00. "
        "I.V.A. 21%: 1.050.000,00. TOTAL A PAGAR: 6.050.000,00.")


def _rec(value, type_, label):
    spec = EntitySpec(label, type_)
    return match_coverage({spec.key: value}, _OCR, [spec]).records[0]


def test_number_supported_evidence_muestra_etiqueta_y_valor() -> None:
    r = _rec("6.050.000,00", SemanticType.NUMBER, "total a pagar")
    assert r.verdict is EntityVerdict.SUPPORTED
    assert r.evidence and "6.050.000,00" in r.evidence       # el valor anclado, en contexto OCR


def test_number_invented_evidence_muestra_contexto_de_etiqueta() -> None:
    r = _rec("9.999,99", SemanticType.NUMBER, "total a pagar")
    assert r.verdict is EntityVerdict.INVENTED
    assert r.evidence and "TOTAL A PAGAR" in r.evidence       # qué hay junto a la etiqueta


def test_number_review_evidence_apunta_a_la_colision() -> None:
    # confunde 'base imponible' con el valor del total → REVIEW; la traza muestra dónde está
    r = _rec("6.050.000,00", SemanticType.NUMBER, "base imponible")
    assert r.verdict is EntityVerdict.REVIEW
    assert r.evidence and "6.050.000,00" in r.evidence


def test_identifier_supported_evidence() -> None:
    r = _rec("30-71234567-8", SemanticType.IDENTIFIER, "CUIT del emisor")
    assert r.verdict is EntityVerdict.SUPPORTED
    assert r.evidence and "30-71234567-8" in r.evidence


def test_unverified_sin_evidence() -> None:
    r = _rec(None, SemanticType.NUMBER, "total a pagar")
    assert r.verdict is EntityVerdict.UNVERIFIED
    assert r.evidence is None


def test_evidence_es_de_una_linea_con_elipsis() -> None:
    # multilínea → el excerpt colapsa whitespace y agrega elipsis
    ocr = "linea uno\nTotal a pagar:\n1.234,56\nlinea final"
    spec = EntitySpec("total a pagar", SemanticType.NUMBER)
    r = match_coverage({spec.key: "1.234,56"}, ocr, [spec]).records[0]
    assert r.verdict is EntityVerdict.SUPPORTED
    assert "\n" not in (r.evidence or "")
