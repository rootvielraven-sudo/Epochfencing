# Autor: @Raven
"""VERA — matching por ROL/PROXIMIDAD para NUMBER (ADR-026 mitigación de colisión)."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict


def _v(value, ocr, label="total a pagar"):
    spec = EntitySpec(label, SemanticType.NUMBER)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


def test_valor_junto_a_etiqueta_supported() -> None:
    ocr = "Factura. Total a pagar: 3.025.000,00 EUR."
    assert _v("3025000.00", ocr) is EntityVerdict.SUPPORTED       # reformateado, junto a etiqueta


def test_etiqueta_con_acentos_y_case() -> None:
    ocr = "TOTAL A PAGAR ............ 1.234,56"
    assert _v("1234.56", ocr) is EntityVerdict.SUPPORTED          # case/leaders, mismo valor


def test_colision_lejana_review() -> None:
    # el valor existe en el doc pero LEJOS de su etiqueta → REVIEW (no SUPPORTED silencioso)
    pad = " texto sin cifras." * 10
    ocr = f"Total a pagar: 1.000,00 EUR.{pad} Referencia lejana: 9.999,99."
    assert _v("9.999,99", ocr) is EntityVerdict.REVIEW


def test_no_esta_en_el_doc_invented() -> None:
    ocr = "Total a pagar: 1.000,00 EUR."
    assert _v("7.777,77", ocr) is EntityVerdict.INVENTED


def test_valor_no_numerico_review() -> None:
    ocr = "Total a pagar: 1.000,00 EUR."
    assert _v("no es un numero", ocr) is EntityVerdict.REVIEW


def test_etiqueta_ausente_pero_valor_presente_review() -> None:
    # la etiqueta del contrato no aparece en el OCR, pero el valor sí (en otro campo) → REVIEW
    ocr = "Importe abonado: 1.000,00 EUR."
    assert _v("1.000,00", ocr, label="saldo pendiente") is EntityVerdict.REVIEW


def test_robustez_abreviatura_puntuada() -> None:
    # 'I.V.A.' (puntos) ≡ etiqueta 'iva' → ancla por tokens
    ocr = "Factura. I.V.A. 21%: 259,26 EUR."
    assert _v("259,26", ocr, label="iva") is EntityVerdict.SUPPORTED


def test_no_matchea_dentro_de_otra_palabra() -> None:
    # igualdad por TOKEN, no substring: 'iva' NO matchea dentro de 'derivado' → REVIEW
    ocr = "Concepto derivado del servicio: 100,00 EUR."
    assert _v("100,00", ocr, label="iva") is EntityVerdict.REVIEW


def test_colision_adyacente_misma_linea_review() -> None:
    # dos valores del mismo tipo en la MISMA línea: el LLM confunde subtotal por el IVA →
    # NO es el número más cercano a 'subtotal' → REVIEW (no SUPPORTED silencioso = el FN cerrado)
    ocr = "Detalle. Subtotal: 1.000,00 EUR   IVA: 210,00 EUR   Total: 1.210,00 EUR."
    assert _v("210,00", ocr, label="subtotal") is EntityVerdict.REVIEW      # valor del vecino
    assert _v("1.000,00", ocr, label="subtotal") is EntityVerdict.SUPPORTED  # valor correcto


def test_porcentaje_se_saltea() -> None:
    # 'I.V.A. 21% ... 259,26': el 21% no es candidato a valor → el más cercano es 259,26
    ocr = "I.V.A. 21% ............ 259,26 EUR."
    assert _v("259,26", ocr, label="iva") is EntityVerdict.SUPPORTED


def test_valor_de_linea_anterior_no_se_cuela() -> None:
    # con líderes punteados, el valor de la línea de ARRIBA no debe anclar el campo de abajo
    ocr = ("Base imponible ............ 1.000,00\n"
           "Total a pagar ............. 1.210,00\n")
    assert _v("1.210,00", ocr, label="total a pagar") is EntityVerdict.SUPPORTED
    assert _v("1.000,00", ocr, label="total a pagar") is EntityVerdict.REVIEW  # es de arriba
