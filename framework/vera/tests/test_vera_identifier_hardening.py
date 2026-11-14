# Autor: @Raven
"""P0.1 — Endurecimiento de IDENTIFIER (brief de arquitectura regulatoria).

El anclaje viejo era `llm_canon in ocr_canon` (substring sobre el OCR canonizado completo) →
falso SUPPORTED cuando el valor del LLM aparece EMBEBIDO dentro de un identificador mayor o como
CONCATENACIÓN de tokens adyacentes. Para banca/legal eso es inaceptable (cuenta/IBAN/CUIT mal
extraído marcado como confirmado).

Fix: matching por FRONTERA de token (igualdad contra runs de identificador, no substring) +
validación de checksum cuando el contrato declara `fmt` (iban/cuit/cuil/nif/cif). Filosofía
regulatoria: cero falsos SUPPORTED; lo dudoso → REVIEW; sin LLM; heurística determinista.
"""
from __future__ import annotations

from framework.vera.vera_coverage import match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType
from framework.vera.vera_tribunal import EntityVerdict

IDN = SemanticType.IDENTIFIER
SUP, INV, REV = EntityVerdict.SUPPORTED, EntityVerdict.INVENTED, EntityVerdict.REVIEW

# IBAN VÁLIDO (mod-97) — ejemplo canónico español
IBAN_OK = "ES9121000418450200051332"


def _v(ocr: str, value: str, *, label: str = "cuenta", fmt: str | None = None) -> EntityVerdict:
    spec = EntitySpec(label, IDN, fmt=fmt)
    return match_coverage({spec.key: value}, ocr, [spec]).records[0].verdict


# ── P0.1: el falso-SUPPORTED por substring debe morir ────────────────────────
def test_substring_embebido_no_es_supported() -> None:
    # valor del LLM embebido DENTRO de un identificador mayor → NUNCA SUPPORTED
    ocr = "Cuenta destino: 9981234567890123456789. Banco."
    assert _v(ocr, "123456789012", label="cuenta destino") is not SUP


def test_concatenacion_adyacente_no_es_supported() -> None:
    # "12 3456" canoniza a "123456" pero son tokens distintos → NUNCA SUPPORTED
    ocr = "Expediente: 9912 3456 7890 del juzgado."
    assert _v(ocr, "991234567890", label="expediente") is not SUP


# ── Casos requeridos por el brief ────────────────────────────────────────────
def test_iban_valido_junto_a_etiqueta_correcta_supported() -> None:
    ocr = f"Datos bancarios. Cuenta: {IBAN_OK}. Titular: ACME SA."
    assert _v(ocr, IBAN_OK, label="cuenta", fmt="iban") is SUP


def test_iban_un_digito_cambiado_invented() -> None:
    bad = IBAN_OK[:10] + str((int(IBAN_OK[10]) + 1) % 10) + IBAN_OK[11:]
    ocr = f"Cuenta: {IBAN_OK}. Banco."
    assert _v(ocr, bad, label="cuenta", fmt="iban") is INV


def test_iban_valido_etiqueta_incorrecta_review() -> None:
    # el IBAN existe en el doc pero NO junto a su etiqueta declarada → rol → REVIEW
    ocr = f"Cuenta del proveedor: {IBAN_OK}.\nCuenta del cliente: ES0000000000000000000000."
    assert _v(ocr, IBAN_OK, label="cuenta del cliente", fmt="iban") is REV


def test_identificador_demasiado_corto_review() -> None:
    assert _v("Ref: AB12.", "AB12", label="ref") is REV   # <6 canónico → no afirmamos


def test_identificador_reformateado_espacios_guiones_supported() -> None:
    spaced = " ".join(IBAN_OK[i:i + 4] for i in range(0, len(IBAN_OK), 4))
    ocr = f"Cuenta: {spaced}. Banco."
    assert _v(ocr, IBAN_OK, label="cuenta", fmt="iban") is SUP            # IBAN con espacios
    cuit_ocr = "CUIT del emisor: 30-71234567-8."
    assert _v(cuit_ocr, "30712345678", label="CUIT del emisor") is SUP   # genérico con guiones


def test_identificador_otro_rol_review() -> None:
    ocr = ("CUIT del emisor: 30-71111111-7\nCUIT del receptor: 30-72222222-2")
    # el LLM puso la del receptor en el campo del emisor → REVIEW
    assert _v(ocr, "30-72222222-2", label="CUIT del emisor") is REV


# ── Checksum determinista (defensa en profundidad) ──────────────────────────
def test_cuit_checksum_invalido_no_es_supported() -> None:
    # CUIT con dígito verificador roto, declarado fmt=cuit → nunca SUPPORTED
    ocr = "CUIT: 30-71234567-0."   # dígito verificador incorrecto (válido es 8)
    assert _v(ocr, "30-71234567-0", label="cuit", fmt="cuit") is not SUP


def test_nif_valido_supported() -> None:
    ocr = "NIF: 12345678Z."   # 12345678 → Z válido (mod-23)
    assert _v(ocr, "12345678Z", label="nif", fmt="nif") is SUP
