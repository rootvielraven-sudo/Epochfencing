# Autor: @Raven
"""VERA — P3 Tanda 2: identificadores varios, id corto, fecha parcial + LÍMITES declarados.

Mapea el envelope. Lo SEGURO (no FP): otros identificadores anclan por existencia canónica
(agnóstico al tipo); id corto <6 → REVIEW; fecha parcial → REVIEW (no SUPPORTED ni INVENTED).
LÍMITES DECLARADOS (documentados, no escondidos): número con espacio de miles → INVENTED;
ruido OCR de dígitos (O↔0) → INVENTED (source corrupto)."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

NUM, DATE, IDN = SemanticType.NUMBER, SemanticType.DATE, SemanticType.IDENTIFIER


def _v(label, typ, ocr, val):
    s = EntitySpec(label, typ)
    return match_coverage({s.key: val}, ocr, [s]).records[0].verdict


# ── SEGURO ───────────────────────────────────────────────────────────────────
def test_otros_identificadores_anclan_por_existencia() -> None:
    assert _v("cuit", IDN, "C.U.I.T.: 30-71234567-8", "30712345678") is EntityVerdict.SUPPORTED
    assert _v("nif", IDN, "NIF: 12345678-Z", "12345678Z") is EntityVerdict.SUPPORTED
    assert _v("cif", IDN, "CIF: B-12345674", "B12345674") is EntityVerdict.SUPPORTED
    assert _v("dni", IDN, "DNI 12.345.678", "12345678") is EntityVerdict.SUPPORTED
    assert _v("doi", IDN, "DOI: 10.1000/abc.123", "10.1000/abc.123") is EntityVerdict.SUPPORTED


def test_identificador_near_miss_invented() -> None:
    assert _v("cuit", IDN, "C.U.I.T.: 30-71234567-8", "30-71234567-9") is EntityVerdict.INVENTED


def test_identificador_corto_review() -> None:
    assert _v("codigo", IDN, "Codigo: AB12", "AB12") is EntityVerdict.REVIEW       # 4 chars
    assert _v("serie", IDN, "Serie: ABC123", "ABC123") is EntityVerdict.SUPPORTED  # 6 chars


def test_fecha_parcial_review_no_fp() -> None:
    # sin día / año 2 dígitos / mes-año → el parser no resuelve → REVIEW (seguro, no FP/FN)
    assert _v("fecha", DATE, "Fecha: 03/2024", "03/2024") is EntityVerdict.REVIEW
    assert _v("fecha", DATE, "Fecha: marzo 2024", "marzo 2024") is EntityVerdict.REVIEW
    assert _v("fecha", DATE, "Fecha: mar-24", "mar-24") is EntityVerdict.REVIEW


# ── LÍMITES DECLARADOS (no es lo deseado; documentado para no esconderlo) ──────
def test_LIMITE_numero_con_espacio_de_miles() -> None:
    # DECLARADO: el espacio como separador de miles no se ancla (el tokenizer parte
    # "1 000 000"). Norma contable es/latam usa ./, → exposición baja. Si un mercado usa
    # espacio de miles (europeo), se resolvería con patrón estricto + re-gate.
    assert _v("total a pagar", NUM, "Total a pagar: 1 000 000,00 EUR", "1.000.000,00") \
        is EntityVerdict.INVENTED


def test_ruido_ocr_digitos_degrada_a_review() -> None:
    # MEJORADO (señal de calidad de OCR): si el OCR leyó '1OO,OO' (letra O por cero), VERA detecta
    # la corrupción confundible y degrada INVENTED→REVIEW (no acusa al LLM de inventar un valor que
    # podría estar; el problema es el escaneo). Antes era un límite declarado (INVENTED).
    assert _v("total a pagar", NUM, "Total a pagar: 1OO,OO EUR", "100,00") is EntityVerdict.REVIEW
