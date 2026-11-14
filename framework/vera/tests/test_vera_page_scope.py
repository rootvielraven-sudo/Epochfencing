# Autor: @Raven
"""P1 — page scope: acota el anclaje de una entidad a una página del OCR multi-página.

En un documento multi-página la misma etiqueta aparece por página ("Total" en pág 1 y 3). Sin
scope, un valor de la página equivocada puede confirmar el campo (falso SUPPORTED por colisión
cross-página). Con ``page`` declarado, la entidad ancla SOLO en esa página (split por form-feed
\\f, el marcador estándar de pdftotext). Valor en OTRA página → REVIEW (existe, pero no donde
debía → no se confirma ni se acusa). Página inexistente → REVIEW.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

NUM = SemanticType.NUMBER
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW

# 3 páginas separadas por form-feed; "Total a pagar" en cada una con valor distinto
_DOC = ("Pagina 1.\nTotal a pagar: 100,00 EUR.\f"
        "Pagina 2.\nTotal a pagar: 500,00 EUR.\f"
        "Pagina 3.\nTotal a pagar: 999,00 EUR.")


def _v(value: str, *, page: int | None = None, ocr: str = _DOC) -> EntityVerdict:
    spec = EntitySpec("total a pagar", NUM, page=page)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def test_valor_de_la_pagina_declarada_supported() -> None:
    assert _v("500.00", page=2) is SUP
    assert _v("999.00", page=3) is SUP


def test_valor_de_otra_pagina_es_review() -> None:
    # 999 está en la página 3, pero el campo declara página 1 → existe pero no ahí → REVIEW
    assert _v("999.00", page=1) is REV


def test_pagina_inexistente_es_review() -> None:
    assert _v("100.00", page=9) is REV


def test_valor_ausente_de_toda_pagina_invented() -> None:
    assert _v("777.00", page=2) is INV


def test_sin_page_comporta_como_siempre() -> None:
    # sin scope, cualquier "Total a pagar" del doc ancla (comportamiento intacto)
    assert _v("999.00") is SUP
    assert _v("500.00") is SUP


def test_doc_sin_marcador_es_una_pagina() -> None:
    ocr = "Total a pagar: 250,00 EUR."
    assert _v("250.00", page=1, ocr=ocr) is SUP
    assert _v("250.00", page=2, ocr=ocr) is REV   # no hay página 2
