# Autor: @Raven
"""VERA — proximidad para DATE e IDENTIFIER (cierra el FN de rol en facturas).

Una factura tiene 2 CUITs y 2 fechas. Antes el anclaje era por existencia → un swap
(CUIT del receptor puesta como del emisor) pasaba SUPPORTED. Ahora ancla por proximidad a
la etiqueta (acotada a la línea) → el swap va a REVIEW."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

DATE, IDN = SemanticType.DATE, SemanticType.IDENTIFIER

_FACT = ("FACTURA A\nFecha de Emision: 15/03/2024\nFecha de Vencimiento: 14/04/2024\n"
         "CUIT del Emisor: 30-71234567-8\nCUIT del Receptor: 30-99887766-5\n")
_C = [EntitySpec("fecha de emision", DATE), EntitySpec("fecha de vencimiento", DATE),
      EntitySpec("CUIT del emisor", IDN), EntitySpec("CUIT del receptor", IDN)]


def _verdicts(vals):
    out = {_C[i].key: vals[i] for i in range(len(_C))}
    return {r.entity_name: r.verdict for r in match_coverage(out, _FACT, _C).records}


def test_factura_correcta_todo_supported() -> None:
    by = _verdicts(["15/03/2024", "14/04/2024", "30-71234567-8", "30-99887766-5"])
    assert all(v is EntityVerdict.SUPPORTED for v in by.values())


def test_factura_swap_va_a_review() -> None:
    # emisor<->receptor y emision<->vto intercambiados → las 4 a REVIEW (rol mal asignado)
    by = _verdicts(["14/04/2024", "15/03/2024", "30-99887766-5", "30-71234567-8"])
    assert all(v is EntityVerdict.REVIEW for v in by.values()), by


def test_fecha_junto_a_su_etiqueta_supported() -> None:
    ocr = "Comprobante. Fecha de emision: 15/03/2024."
    s = EntitySpec("fecha de emision", DATE)
    assert match_coverage({s.key: "2024-03-15"}, ocr, [s]).records[0].verdict \
        is EntityVerdict.SUPPORTED


def test_identificador_junto_a_su_etiqueta_supported() -> None:
    ocr = "Datos. CUIT del emisor: 30-71234567-8."
    s = EntitySpec("CUIT del emisor", IDN)
    assert match_coverage({s.key: "30712345678"}, ocr, [s]).records[0].verdict \
        is EntityVerdict.SUPPORTED


def test_etiqueta_no_reconocida_va_a_review() -> None:
    # el valor existe pero la etiqueta del contrato no aparece (genérica) → REVIEW (no FP)
    ocr = "Documento. C.U.I.T.: 30-71234567-8."
    s = EntitySpec("CUIT del emisor", IDN)   # 'C.U.I.T.' no contiene 'del emisor'
    assert match_coverage({s.key: "30712345678"}, ocr, [s]).records[0].verdict \
        is EntityVerdict.REVIEW
