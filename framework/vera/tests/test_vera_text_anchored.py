# Autor: @Raven
"""P2 — TEXT_ANCHORED: confirma texto corto por presencia VERBATIM (tokens) junto a la etiqueta.

TEXT plano → UNVERIFIED (sin anclaje). TEXT_ANCHORED sube a SUPPORTED un valor cuyo texto aparece
en el documento (insensible a case/acentos/puntuación, a nivel de tokens contiguos). Conservador:
junto a su etiqueta → SUPPORTED; presente pero en otro lado → REVIEW; ausente o trivial (corto) →
REVIEW. NUNCA INVENTED — un texto reformulado no es alucinación demostrable, no se acusa en falso.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

TA, TEXT = SemanticType.TEXT_ANCHORED, SemanticType.TEXT
SUP, INV, REV, UNV = (EntityVerdict.SUPPORTED, EntityVerdict.INVENTED,
                      EntityVerdict.REVIEW, EntityVerdict.UNVERIFIED)


def _v(ocr: str, value: str, *, label: str = "cliente", t: SemanticType = TA) -> EntityVerdict:
    spec = EntitySpec(label, t)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def test_presente_junto_a_etiqueta_supported() -> None:
    assert _v("Cliente: ACME SA. Total: 100,00 EUR.", "ACME SA") is SUP


def test_case_acentos_insensible_supported() -> None:
    assert _v("Razon social: José Pérez González.", "jose perez gonzalez",
              label="razon social") is SUP


def test_ausente_es_review_no_invented() -> None:
    # texto reformulado/ausente → REVIEW (no acusamos INVENTED)
    assert _v("Cliente: ACME SA.", "Beta Sociedad Anonima") is REV


def test_otro_rol_es_review() -> None:
    ocr = "Vendedor: ACME SA. Comprador: BETA SRL."
    assert _v(ocr, "ACME SA", label="comprador") is REV   # ACME no está junto a 'comprador'


def test_demasiado_corto_es_review() -> None:
    assert _v("Tipo: SA.", "SA", label="tipo") is REV     # <4 chars → no se confirma


def test_text_plano_sigue_unverified() -> None:
    assert _v("Cliente: ACME SA.", "ACME SA", t=TEXT) is UNV   # TEXT plano no ancla


def test_text_anchored_optional_no_bloquea_ok() -> None:
    # un TEXT_ANCHORED optional ausente (REVIEW) no debe bloquear ok
    contract = [EntitySpec("total", SemanticType.NUMBER),
                EntitySpec("cliente", TA, optional=True)]
    out = {"total": "100.00", "cliente": "Nombre Inexistente"}
    rep = match_coverage(out, "Total: 100,00 EUR.", contract)
    assert rep.records[1].verdict is REV
    assert rep.ok is True
