# Autor: @Raven
"""VERA paso 4 — anclaje canónico de identificadores (IBAN/CUIT/NIF/CIF). ADR-022."""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_identifiers import anchor_identifier, canon_id
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW


def test_canon_quita_separadores_y_mayuscula() -> None:
    assert canon_id("es76 2100-0000.0000") == "ES76210000000000"


def test_iban_espaciado_sin_fmt_es_review() -> None:
    # P0.1: IBAN espaciado SIN fmt declarado → REVIEW (no confirmamos que los grupos separados
    # por espacio son un solo identificador; no acusamos INVENTED ni afirmamos SUPPORTED).
    ocr = "Cuenta: ES76 2100 0000 0000 0123 4567. Importe 100 EUR."
    assert anchor_identifier("ES7621000000000001234567", ocr) is REV


def test_iban_con_fmt_y_checksum_valido_ancla() -> None:
    # con fmt=iban + checksum válido, el IBAN ancla SUPPORTED esté espaciado o no en el OCR.
    iban = "ES9121000418450200051332"   # mod-97 válido
    spaced = " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
    ocr = f"Cuenta: {spaced}. Importe 100 EUR."
    assert anchor_identifier(iban, ocr, fmt="iban") is SUP
    assert anchor_identifier(spaced, ocr, fmt="iban") is SUP


def test_cuit_con_guiones_ancla() -> None:
    ocr = "Emisor CUIT 20-12345678-9. Factura A."
    assert anchor_identifier("20123456789", ocr) is SUP
    assert anchor_identifier("20-12345678-9", ocr) is SUP


def test_identificador_inventado() -> None:
    ocr = "Cuenta: ES76 2100 0000 0000 0123 4567."
    assert anchor_identifier("ES9999999999999999999999", ocr) is INV


def test_demasiado_corto_es_review() -> None:
    assert anchor_identifier("AB12", "texto AB12 cualquiera") is REV   # <6 → no se ancla


def test_match_coverage_identifier() -> None:
    iban = "ES9121000418450200051332"   # mod-97 válido
    spaced = " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))
    ocr = f"Contrato. IBAN: {spaced}. Total: 1.000,00 EUR."
    contract = [
        EntitySpec("IBAN", SemanticType.IDENTIFIER, fmt="iban"),   # contrato declara el formato
        EntitySpec("total", SemanticType.NUMBER),
    ]
    out = {"IBAN": iban, "total": "1000.00"}     # IBAN canónico, checksum válido
    assert match_coverage(out, ocr, contract).ok
    out_inv = {"IBAN": "ES0000000000000000000000", "total": "1000.00"}
    rep = match_coverage(out_inv, ocr, contract)
    assert not rep.ok
    iban_rec = next(r for r in rep.records if r.entity_name == "IBAN")
    assert iban_rec.verdict is EntityVerdict.INVENTED
