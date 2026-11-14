# Autor: @Raven
"""Regresión permanente de los 2 FN del path NUMBER (brief HADD).

FN-1 adjacent collision: subtotal + IVA en la MISMA línea/ventana. El anclaje debe resolver
por proximidad al LABEL PROPIO de cada entidad (subtotal→su label, IVA→su label), no por el
primer match de valor. Si el LLM confunde un campo por el vecino → REVIEW (degradación HADD
aceptable), nunca SUPPORTED silencioso.

FN-2 currency: el marcador de moneda se normaliza en AMBOS lados (no rompe el anclaje, no es
FP). Si el LLM declara una moneda DISTINTA a la del OCR para ese importe → INVENTED. Si el LLM
devuelve el número pelado (sin moneda) → la divisa no es verificable → SUPPORTED por magnitud
(CASO 2 declarado: no es alucinación, la magnitud SÍ está en la fuente).

Invariante sagrado: FP=0. Nunca decir INVENTED de algo que SÍ está en la fuente.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

NUM = SemanticType.NUMBER
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW

# subtotal e IVA en la MISMA línea (caso contable del brief)
_ADJ = "Detalle. Subtotal: 1.000,00 EUR   IVA: 210,00 EUR"
_C_ADJ = [EntitySpec("subtotal", NUM), EntitySpec("IVA", NUM)]


def _verdicts(ocr, contract, out):
    return {r.entity_name: r.verdict for r in match_coverage(out, ocr, contract).records}


# ── FN-1 adjacent collision ──────────────────────────────────────────────────
def test_adjacent_correcto_ambos_supported() -> None:
    # extracción CORRECTA → cada valor ancla a SU label → SUPPORTED (no se degrada de más)
    by = _verdicts(_ADJ, _C_ADJ, {"subtotal": "1.000,00", "IVA": "210,00"})
    assert by["subtotal"] is SUP
    assert by["IVA"] is SUP


def test_adjacent_swap_va_a_review_no_supported() -> None:
    # el LLM confunde: subtotal=210(=IVA del vecino), IVA=1.000(=subtotal) → NO SUPPORTED
    by = _verdicts(_ADJ, _C_ADJ, {"subtotal": "210,00", "IVA": "1.000,00"})
    assert by["subtotal"] is not SUP, "FN-1: el vecino no debe anclar como propio"
    assert by["IVA"] is not SUP
    # degradación HADD aceptable: REVIEW (existe en el doc pero no junto a SU etiqueta)
    assert by["subtotal"] is REV
    assert by["IVA"] is REV


# ── FN-2 currency ────────────────────────────────────────────────────────────
def _v(ocr, val, label="total a pagar"):
    s = EntitySpec(label, NUM)
    return match_coverage({s.key: val}, ocr, [s]).records[0].verdict


def test_currency_marcador_no_es_FP() -> None:
    # INVARIANTE FP=0: '$1.000,00' del LLM vs '1.000,00' en OCR (sin moneda) → el marcador se
    # normaliza, la magnitud coincide → SUPPORTED, JAMÁS INVENTED.
    assert _v("Total a pagar: 1.000,00", "$1.000,00") is SUP
    assert _v("Total a pagar: 1.000,00", "USD 1.000,00") is SUP


def test_currency_moneda_distinta_es_invented() -> None:
    # el LLM declara EUR pero el OCR muestra USD para ese importe → alucinación de moneda
    assert _v("Total a pagar: 100,00 USD", "100,00 EUR") is INV
    assert _v("Total a pagar: 100,00 USD", "€100,00") is INV


def test_currency_caso2_pelado_supported_declarado() -> None:
    # el LLM devuelve el número sin moneda → divisa no verificable, magnitud SÍ está → SUPPORTED
    assert _v("Total a pagar: 100,00 USD", "100,00") is SUP
