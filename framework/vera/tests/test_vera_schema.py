# Autor: @Raven
"""VERA Fase 0 — esquema canónico (form-only) + revalidación. ADR-022 pilar de cobertura."""
from __future__ import annotations

import json

from framework.vera.vera_schema import (
    EntitySpec,
    SemanticType,
    build_llm_schema,
    build_prompt_fragment,
    build_type_map,
    validate_output,
)

_CONTRACT = [
    EntitySpec("total a pagar", SemanticType.NUMBER),
    EntitySpec("fecha de vencimiento", SemanticType.DATE),
    EntitySpec("IBAN", SemanticType.IDENTIFIER),
]


# ── builder: forma ───────────────────────────────────────────────────────────
def test_schema_es_objeto_cerrado_todo_required_string_o_null() -> None:
    s = build_llm_schema(_CONTRACT)
    assert s["type"] == "object"
    assert s["additionalProperties"] is False                 # cierra inventos de clave
    assert s["required"] == ["total_a_pagar", "fecha_de_vencimiento", "IBAN"]  # claves slug + orden
    for prop in s["properties"].values():
        assert prop == {"type": ["string", "null"]}           # solo string|null


def test_schema_NO_lleva_tipo_semantico() -> None:
    # Invariante de separación: el tipo (number/date/identifier) NO puede filtrarse al LLM.
    blob = json.dumps(build_llm_schema(_CONTRACT))
    for forbidden in ("number", "date", "identifier", "integer", "pattern", "format"):
        assert forbidden not in blob, f"el schema del LLM no debe contener '{forbidden}'"


def test_type_map_vive_solo_en_vera() -> None:
    tm = build_type_map(_CONTRACT)
    assert tm == {                       # type-map es por NOMBRE humano (solo VERA)
        "total a pagar": SemanticType.NUMBER,
        "fecha de vencimiento": SemanticType.DATE,
        "IBAN": SemanticType.IDENTIFIER,
    }
    # y NO aparece en el schema del LLM (doble chequeo de separación)
    assert "number" not in json.dumps(build_llm_schema(_CONTRACT))


# ── revalidación: cinturón y tirantes ────────────────────────────────────────
def test_output_bien_formado_pasa() -> None:
    out = {"total_a_pagar": "1.234,56", "fecha_de_vencimiento": "15/03/2024", "IBAN": "ES76..."}
    assert validate_output(out, _CONTRACT).ok


def test_null_explicito_es_valido() -> None:
    out = {"total_a_pagar": "10,00", "fecha_de_vencimiento": None, "IBAN": None}
    assert validate_output(out, _CONTRACT).ok


def test_ausencia_de_campo_es_error_no_null_implicito() -> None:
    # null-explícito OBLIGATORIO: omitir un campo NO es válido (debe venir null).
    out = {"total_a_pagar": "10,00", "fecha_de_vencimiento": "15/03/2024"}   # falta IBAN
    r = validate_output(out, _CONTRACT)
    assert not r.ok and any("IBAN" in e or "required" in e for e in r.errors)


def test_clave_extra_rechazada_additionalProperties() -> None:
    out = {"total_a_pagar": "10,00", "fecha_de_vencimiento": None, "IBAN": None, "colado": "x"}
    r = validate_output(out, _CONTRACT)
    assert not r.ok   # additionalProperties:false lo enforca VERA siempre


def test_valor_no_string_ni_null_rechazado() -> None:
    # el LLM debe escribir el valor como string; un number/objeto/array crudo se rechaza
    for bad in (1234, {"x": 1}, ["a"], True):
        out = {"total_a_pagar": bad, "fecha_de_vencimiento": None, "IBAN": None}
        assert not validate_output(out, _CONTRACT).ok, f"{bad!r} no debería validar"


def test_contrato_vacio() -> None:
    assert validate_output({}, []).ok                       # nada declarado → {} válido
    assert not validate_output({"x": "1"}, []).ok           # pero cerrado: sin extras


# ── Fase A: fragmento-de-prompt derivado del contrato ────────────────────────
def _fragment_names(frag: str) -> list[str]:
    return [ln[2:] for ln in frag.splitlines() if ln.startswith("- ")]


def test_las_tres_salidas_comparten_nombres_y_orden() -> None:
    # no-desincronización: derivan del MISMO contrato. El schema usa CLAVES (slug),
    # el type-map y el fragmento usan NOMBRES (humano); ambos en 1:1 con el contrato.
    claves_schema = build_llm_schema(_CONTRACT)["required"]
    nombres_typemap = list(build_type_map(_CONTRACT).keys())
    nombres_fragmento = _fragment_names(build_prompt_fragment(_CONTRACT))
    assert claves_schema == [e.key for e in _CONTRACT] == [
        "total_a_pagar", "fecha_de_vencimiento", "IBAN",
    ]
    assert nombres_typemap == nombres_fragmento == [e.name for e in _CONTRACT] == [
        "total a pagar", "fecha de vencimiento", "IBAN",
    ]


def test_fragmento_pide_null_explicito() -> None:
    frag = build_prompt_fragment(_CONTRACT).lower()
    assert "null" in frag and ("no lo omitas" in frag or "no inventes" in frag)


def test_fragmento_NO_lleva_tipo_semantico() -> None:
    # análogo a schema_NO_lleva_tipo_semantico: el fragmento no filtra tipo ni canonicalización
    frag = build_prompt_fragment(_CONTRACT).lower()
    for forbidden in ("number", "identifier", "integer", "pattern", "format",
                      "dd/mm", "checksum", "numérico", "número", "validá"):
        assert forbidden not in frag, f"el fragmento no debe contener '{forbidden}'"


def test_fragmento_contrato_vacio_es_vacio() -> None:
    assert build_prompt_fragment([]) == ""
