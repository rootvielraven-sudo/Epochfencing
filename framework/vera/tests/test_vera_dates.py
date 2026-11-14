# Autor: @Raven
"""VERA paso 3 — extractor/anclaje canónico de fechas. ADR-022."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_dates import anchor_date, parse_dates
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


# ── parseo canónico ──────────────────────────────────────────────────────────
def test_parse_formatos_inequivocos() -> None:
    assert parse_dates("2024-03-15") == [frozenset({(2024, 3, 15)})]
    assert parse_dates("15/03/2024") == [frozenset({(2024, 3, 15)})]   # día 15>12 → inequívoco
    assert parse_dates("15 de marzo de 2024") == [frozenset({(2024, 3, 15)})]


def test_parse_numerica_ambigua() -> None:
    # 05/03/2024: ambos ≤12 → dos lecturas (dd/mm y mm/dd)
    assert parse_dates("05/03/2024") == [frozenset({(2024, 3, 5), (2024, 5, 3)})]


# ── anclaje ──────────────────────────────────────────────────────────────────
def test_supported_literal() -> None:
    assert anchor_date("15/03/2024", "Fecha: 15/03/2024.") is SUP


def test_supported_reformateado() -> None:
    # mismo valor, OTRO formato → DEBE anclar (cazador de FP de fecha)
    assert anchor_date("2024-03-15", "Vence el 15/03/2024.") is SUP
    assert anchor_date("15/03/2024", "Vence el 15 de marzo de 2024.") is SUP
    assert anchor_date("15 de marzo de 2024", "Vence el 2024-03-15.") is SUP


def test_invented() -> None:
    assert anchor_date("01/01/2020", "Fecha: 15/03/2024.") is INV


def test_review_ambiguo_swap() -> None:
    # 03/05 vs 05/03: ambos ≤12, podría ser swap dd/mm↔mm/dd → no se afirma → REVIEW
    assert anchor_date("03/05/2024", "Fecha: 05/03/2024.") is REV


def test_review_ocr_ambiguo_llm_inequivoco() -> None:
    # LLM commitió 2024-03-05 (inequívoco); OCR "05/03/2024" es ambiguo → no se confirma → REVIEW
    assert anchor_date("2024-03-05", "Fecha: 05/03/2024.") is REV


# ── integración con match_coverage (tipo DATE) ───────────────────────────────
def test_match_coverage_date() -> None:
    ocr = "Contrato. Fecha de vencimiento: 15/03/2024. Total: 1.000,00 EUR."
    contract = [
        EntitySpec("fecha de vencimiento", SemanticType.DATE),
        EntitySpec("total", SemanticType.NUMBER),
    ]
    out = {"fecha_de_vencimiento": "2024-03-15", "total": "1000.00"}   # ambos reformateados
    rep = match_coverage(out, ocr, contract)
    assert rep.ok
    out_inv = {"fecha_de_vencimiento": "01/01/2000", "total": "1000.00"}
    rep2 = match_coverage(out_inv, ocr, contract)
    assert not rep2.ok
    fecha = next(r for r in rep2.records if r.entity_name == "fecha de vencimiento")
    assert fecha.verdict is EntityVerdict.INVENTED
