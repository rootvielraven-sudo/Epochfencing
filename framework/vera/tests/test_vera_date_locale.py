# Autor: @Raven
"""P1 — DATE locale: desambigua dd/mm↔mm/dd cuando el contrato declara el locale.

Sin locale una fecha numérica con día y mes ambos ≤12 (01/04/2024) es ambigua → REVIEW (seguro
pero ruidoso en banca AR/ES). Con locale declarado (es-AR/es-ES → dd/mm, en-US → mm/dd) se resuelve
DETERMINÍSTICAMENTE — heurística reproducible y DECLARADA, no una convención hardcodeada.

Invariante: el locale solo COLAPSA la ambigüedad; aplicado simétricamente a OCR y output, nunca
inventa coincidencia → cero falsos SUPPORTED.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

DATE = SemanticType.DATE
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


def _v(ocr: str, value: str, *, label: str = "fecha", locale: str | None = None) -> EntityVerdict:
    spec = EntitySpec(label, DATE, locale=locale)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def test_ambigua_sin_locale_sigue_review() -> None:
    assert _v("Fecha de emision: 01/04/2024.", "01/04/2024") is REV   # comportamiento actual


def test_ambigua_con_locale_es_ar_supported() -> None:
    assert _v("Fecha de emision: 01/04/2024.", "01/04/2024", locale="es-AR") is SUP


def test_ambigua_con_locale_en_us_supported() -> None:
    assert _v("Fecha de emision: 04/01/2024.", "04/01/2024", locale="en-US") is SUP


def test_locale_no_crea_falso_supported() -> None:
    # el LLM da una fecha DISTINTA → no SUPPORTED aunque haya locale (no inventa coincidencia)
    assert _v("Fecha de emision: 01/04/2024.", "05/06/2024", locale="es-AR") is INV


def test_locale_iso_cruza_con_dd_mm() -> None:
    # OCR dd/mm con locale es-AR (01/04 = 1 abril) ≡ LLM en ISO 2024-04-01 → SUPPORTED
    assert _v("Fecha: 01/04/2024.", "2024-04-01", locale="es-AR") is SUP
    # pero el mismo OCR contra ISO 2024-01-04 (4 enero) → NO coincide → INVENTED
    assert _v("Fecha: 01/04/2024.", "2024-01-04", locale="es-AR") is INV


def test_inequivoca_sin_locale_sigue_supported() -> None:
    # día >12 ya era inequívoca; el locale no cambia nada
    assert _v("Fecha: 25/03/2024.", "25/03/2024") is SUP
