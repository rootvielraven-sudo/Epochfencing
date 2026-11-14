# Autor: @Raven
"""VERA — chequeo de moneda en cobertura (CASO 1 del FN de moneda, ADR-026/P2).

CASO 1 (el LLM declara una moneda distinta a la del OCR para ese importe) → INVENTED.
CASO 2 (el LLM devuelve el número pelado, sin moneda) → SUPPORTED (límite declarado: NUMBER
verifica magnitud; la divisa solo se chequea si el LLM la emite). Cero FP en valores sin moneda."""
from __future__ import annotations

from framework.vera.vera_coverage import _numbers_with_pos, match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict


def _v(value, ocr, label="total a pagar"):
    s = EntitySpec(label, SemanticType.NUMBER)
    return match_coverage({s.key: value}, ocr, [s]).records[0]


def test_numbers_with_pos_capta_moneda() -> None:
    # precondición del fix: el parser de posición trae la moneda del OCR
    got = {cur for _m, _s, _e, _p, cur in _numbers_with_pos("Total a pagar: 100,00 USD.")}
    assert "USD" in got


def test_caso1_moneda_distinta_codigo_invented() -> None:
    r = _v("100,00 EUR", "Total a pagar: 100,00 USD.")
    assert r.verdict is EntityVerdict.INVENTED
    assert "moneda" in r.reason.lower()


def test_caso1_moneda_distinta_simbolo_invented() -> None:
    r = _v("€100,00", "Total a pagar: 100,00 USD.")
    assert r.verdict is EntityVerdict.INVENTED


def test_misma_moneda_supported() -> None:
    assert _v("100,00 USD", "Total a pagar: 100,00 USD.").verdict is EntityVerdict.SUPPORTED


def test_caso2_valor_pelado_supported_limite_declarado() -> None:
    # el LLM no emite moneda → no se puede saber qué quiso → SUPPORTED (límite declarado)
    assert _v("100,00", "Total a pagar: 100,00 USD.").verdict is EntityVerdict.SUPPORTED


def test_ocr_sin_moneda_no_contradice() -> None:
    # el OCR no especifica moneda → agregar una en el LLM no es contradicción → SUPPORTED
    assert _v("100,00 EUR", "Total a pagar: 100,00.").verdict is EntityVerdict.SUPPORTED


def test_moneda_ok_pero_magnitud_mal_sigue_invented() -> None:
    # magnitud que no está → INVENTED por magnitud (no por moneda)
    assert _v("999,99 USD", "Total a pagar: 100,00 USD.").verdict is EntityVerdict.INVENTED
