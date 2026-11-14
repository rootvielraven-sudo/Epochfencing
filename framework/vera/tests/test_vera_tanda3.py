# Autor: @Raven
"""VERA — P3 Tanda 3: etiqueta repetida, palabras extra, multi-entidad, tipo TEXT."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

NUM, IDN, TEXT = SemanticType.NUMBER, SemanticType.IDENTIFIER, SemanticType.TEXT


def _v(label, typ, ocr, val):
    s = EntitySpec(label, typ)
    return match_coverage({s.key: val}, ocr, [s]).records[0].verdict


_REP = "Resumen. Total a pagar: 1.000,00 EUR. Detalle. Total a pagar: 2.000,00 EUR."


def test_etiqueta_repetida_ancla_cualquier_ocurrencia() -> None:
    # misma etiqueta 2 veces: un valor que coincide con CUALQUIER ocurrencia → SUPPORTED
    assert _v("total a pagar", NUM, _REP, "1.000,00") is EntityVerdict.SUPPORTED
    assert _v("total a pagar", NUM, _REP, "2.000,00") is EntityVerdict.SUPPORTED
    # un valor que no es ninguna → INVENTED
    assert _v("total a pagar", NUM, _REP, "9.999,99") is EntityVerdict.INVENTED


def test_palabra_extra_en_medio_review() -> None:
    assert _v("total a pagar", NUM, "Total general a pagar: 100,00 EUR.", "100,00") \
        is EntityVerdict.REVIEW


def test_palabra_extra_al_final_supported() -> None:
    assert _v("total a pagar", NUM, "Total a pagar bruto: 100,00 EUR.", "100,00") \
        is EntityVerdict.SUPPORTED


def test_contrato_un_token_supported() -> None:
    assert _v("total", NUM, "Total a pagar: 100,00 EUR.", "100,00") is EntityVerdict.SUPPORTED


def test_contrato_mas_largo_que_ocr_review() -> None:
    assert _v("total general a pagar", NUM, "Total a pagar: 100,00 EUR.", "100,00") \
        is EntityVerdict.REVIEW


def test_tipo_text_unverified() -> None:
    assert _v("nombre", TEXT, "Nombre: Juan Perez.", "Juan Perez") is EntityVerdict.UNVERIFIED


_MULTI_OCR = ("FACTURA. Base imponible: 100.000,00 EUR. IVA 21%: 21.000,00 EUR. "
              "Total a pagar: 121.000,00 EUR. CUIT: 30-71234567-8.")
_MULTI_C = [EntitySpec("base imponible", NUM), EntitySpec("iva", NUM),
            EntitySpec("total a pagar", NUM), EntitySpec("CUIT", IDN)]


def test_multientidad_cada_una_independiente() -> None:
    out = {EntitySpec("base imponible", NUM).key: "100.000,00",
           EntitySpec("iva", NUM).key: "21.000,00",
           EntitySpec("total a pagar", NUM).key: "121.000,00",
           EntitySpec("CUIT", IDN).key: "30712345678"}
    rep = match_coverage(out, _MULTI_OCR, _MULTI_C)
    assert rep.ok
    assert all(r.verdict is EntityVerdict.SUPPORTED for r in rep.records)


def test_multientidad_confusion_cruzada_review() -> None:
    # el LLM confunde 'base imponible' con el valor del IVA → REVIEW (proximidad lo atrapa)
    out = {EntitySpec("base imponible", NUM).key: "21.000,00",  # = IVA
           EntitySpec("iva", NUM).key: "21.000,00",
           EntitySpec("total a pagar", NUM).key: "121.000,00",
           EntitySpec("CUIT", IDN).key: "30712345678"}
    rep = match_coverage(out, _MULTI_OCR, _MULTI_C)
    by = {r.entity_name: r.verdict for r in rep.records}
    assert by["base imponible"] is EntityVerdict.REVIEW
    assert by["iva"] is EntityVerdict.SUPPORTED
