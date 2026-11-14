# Autor: @Raven
"""P1 — PERIOD: ancla períodos (mes-año / trimestre / año), no fechas-día.

Extractos, nóminas y facturas tienen un PERÍODO ("03/2024", "marzo 2024", "Q1 2024") que hoy,
tipado como DATE, da REVIEW (fecha parcial). PERIOD canonicaliza a (año, mes) / (año, trimestre)
/ (año) y ancla por existencia junto a la etiqueta — agnóstico al formato, sin confundir con
una fecha-día (01/04/2024 NO es el período "abril 2024").

Filosofía: cero falsos SUPPORTED; lo no parseable / fuera de rol → REVIEW; determinista, sin LLM.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

PERIOD = SemanticType.PERIOD
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


def _v(ocr: str, value: str, *, label: str = "periodo") -> EntityVerdict:
    spec = EntitySpec(label, PERIOD)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def test_mes_anio_numerico_supported() -> None:
    assert _v("Periodo liquidado: 03/2024.", "03/2024") is SUP


def test_mes_anio_cross_formato_supported() -> None:
    # OCR numérico, LLM textual → mismo período canónico → SUPPORTED
    assert _v("Periodo: 03/2024.", "marzo 2024") is SUP
    assert _v("Periodo: marzo de 2024.", "03/2024") is SUP


def test_trimestre_supported() -> None:
    assert _v("Trimestre informado: Q1 2024.", "Q1 2024", label="trimestre informado") is SUP


def test_mes_distinto_invented() -> None:
    assert _v("Periodo: 03/2024.", "04/2024") is INV


def test_fecha_dia_no_es_periodo() -> None:
    # el doc tiene una FECHA-día (01/04/2024), no el período "abril 2024" → no SUPPORTED
    assert _v("Fecha de emision: 01/04/2024.", "04/2024") is not SUP


def test_rol_periodo_equivocado_review() -> None:
    ocr = "Periodo facturado: 03/2024.\nPeriodo anterior: 02/2024."
    assert _v(ocr, "02/2024", label="periodo facturado") is REV


def test_no_parseable_review() -> None:
    assert _v("Periodo: 03/2024.", "cualquier cosa") is REV
