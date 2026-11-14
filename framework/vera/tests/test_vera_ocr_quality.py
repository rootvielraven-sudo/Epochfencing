# Autor: @Raven
"""P1.2 — señal de calidad de OCR: distinguir 'el LLM inventó' de 'el OCR está roto'.

Hoy un valor no encontrado → INVENTED. Pero si el OCR corrompió el dígito (`1OO.OOO,OO` con
letra O por scanner malo), el valor del LLM es CORRECTO y VERA lo acusa en falso. La señal:
cuando un valor iría a INVENTED, si junto a su etiqueta hay un token que es una versión
OCR-CONFUNDIDA del valor (O↔0, l↔1, S↔5, B↔8…) → degrada a REVIEW ('posible corrupción de OCR').

Regla de oro: SOLO degrada INVENTED→REVIEW; NUNCA promueve a SUPPORTED (eso crearía falsos
SUPPORTED). Determinista, sin LLM. Un cambio de valor real (no confusable) sigue → INVENTED.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

NUM, IDN = SemanticType.NUMBER, SemanticType.IDENTIFIER
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


def _v(ocr, value, *, label="total a pagar", t=NUM, **kw):
    spec = EntitySpec(label, t, **kw)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0]


def test_ocr_corrupto_degrada_a_review() -> None:
    # OCR rompió los 0 → letra O; el valor del LLM es correcto → REVIEW, NO INVENTED
    rec = _v("Total a pagar: 1OO.OOO,OO EUR", "100000.00")
    assert rec.verdict is REV
    assert "ocr" in rec.reason.lower()


def test_alucinacion_real_sigue_invented() -> None:
    # valor genuinamente ausente, sin token confundible en el doc → INVENTED (no se degrada)
    assert _v("Total a pagar: 100,00 EUR.", "777777.00").verdict is INV


def test_valor_limpio_presente_supported() -> None:
    assert _v("Total a pagar: 100.000,00 EUR.", "100000.00").verdict is SUP


def test_cambio_de_valor_no_confusable_sigue_invented() -> None:
    # el LLM cambió un dígito por OTRO dígito (no es confusión de OCR) → INVENTED
    assert _v("Total a pagar: 100.000,00 EUR.", "100007.00").verdict is INV


def test_identificador_ocr_corrupto_degrada_a_review() -> None:
    # IBAN con O por 0 en el OCR → REVIEW (el valor podría estar, el scan está dañado)
    iban = "ES9121000418450200051332"
    corrupt = iban.replace("0", "O")            # scanner leyó O en vez de 0
    rec = _v(f"Cuenta: {corrupt}.", iban, label="cuenta", t=IDN)
    assert rec.verdict is REV
