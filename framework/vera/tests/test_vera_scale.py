# Autor: @Raven
"""VERA — escala por palabra en contexto monetario (P3 Tanda 1). Cierra el FP de "millones".

"1,5 millones de EUR" se expande a 1.500.000 → la extracción correcta ancla. "Mil gracias" /
"mil personas" NO se escalan (no hay dinero después) → sin falso escalado."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict
from framework.vera.vera_tribunal import parse_numeric_mentions as P

NUM = SemanticType.NUMBER


def _v(ocr, val, label="total a pagar"):
    s = EntitySpec(label, NUM)
    return match_coverage({s.key: val}, ocr, [s]).records[0].verdict


def _mag(s):
    return [str(m.magnitude) for m in P(s) if m.is_amount]


def test_parser_expande_escala_en_contexto_monetario() -> None:
    assert _mag("1,5 millones de EUR") == ["1500000.0"]
    assert _mag("2 millones de pesos") == ["2000000"]
    assert _mag("5 mil euros") == ["5000"]


def test_parser_no_escala_sin_dinero() -> None:
    # "Mil gracias" / "mil personas" NO son escala → no se escala el número previo
    assert _mag("1.000,00. Mil gracias") == ["1000.00"]
    assert _mag("Total: 1.000,00 Mil gracias") == ["1000.00"]


def test_cobertura_millones_palabra_supported() -> None:
    # el FP que encontramos: OCR en palabras, LLM en dígitos → ahora SUPPORTED
    assert _v("Total a pagar: 1,5 millones de EUR.", "1.500.000,00") is EntityVerdict.SUPPORTED
    assert _v("Total a pagar: 2 millones de pesos.", "2.000.000,00") is EntityVerdict.SUPPORTED
    assert _v("Total a pagar: 5 mil euros.", "5.000,00") is EntityVerdict.SUPPORTED


def test_cobertura_sufijo_M_sigue_andando() -> None:
    assert _v("Total a pagar: 1.000.000,00 EUR.", "1M") is EntityVerdict.SUPPORTED
    assert _v("Total a pagar: 1.500.000,00 EUR.", "1,5M") is EntityVerdict.SUPPORTED


def test_critical_amounts_expande_escala_igual_que_mentions() -> None:
    # bug corregido: parse_critical_amounts/_labeled_amounts usaban suffix=6 → NO veían
    # 'millones' (la palabra no entra en 6 chars) mientras parse_numeric_mentions (16) sí →
    # CompletenessJudge/ArithmeticCoherence daban NOT_FOUND/CONTRADICTED falsos.
    from framework.vera.vera_tribunal import parse_critical_amounts
    t = "Total a pagar: 2 millones de EUR"
    assert _mag(t) == ["2000000"]
    assert [str(x) for x in parse_critical_amounts(t)] == ["2000000"]   # antes daba ['2']


def test_residual_declarado_sin_contexto_monetario() -> None:
    # residual conocido: bare "2 millones" (sin de/moneda) no se escala → no ancla 2.000.000
    # (no es FP silencioso: da INVENTED/REVIEW, queda para el humano). Documenta el límite.
    v = _v("Total a pagar: 2 millones.", "2.000.000,00")
    assert v is not EntityVerdict.SUPPORTED
