# Autor: @Raven
"""VERA — esquema canónico de extracción (Fase 0, ADR-022 pilar de cobertura).

FUENTE ÚNICA del formato. Del CONTRATO de dominio (nombres + tipo semántico) se derivan
DOS artefactos, sin que el tipo se le filtre nunca al LLM:

  build_llm_schema(contract)  → JSON Schema SOLO FORMA (claves + valores string|null).
      Sirve en las dos puntas:
        (a) VERA lo usa para REVALIDAR el output recibido (rechazo determinista).
        (b) se le impone al LLM como SALIDA FORZADA (Claude: input_schema de un tool;
            Gemini: response_schema — fast-follow). NO contiene tipo semántico.

  build_type_map(contract)    → {nombre: SemanticType}. Vive SOLO en VERA; dice cómo
      canonicalizar cada entidad al anclar contra el OCR. El LLM nunca lo ve.

Invariantes de FORMA (decisiones Fase 0):
  - additionalProperties:false SIEMPRE (lo enforca VERA en revalidación, no el proveedor).
  - null-explícito OBLIGATORIO: todos los campos required; ausencia = error, "no
    encontrado" = null explícito (afirmación positiva, hace medible UNVERIFIED).
  - valores SOLO string|null (sin number/date/pattern/format) → subconjunto cumplible
    como constraint en Claude/Gemini/OpenAI.

LÍMITE (separación nítida): el esquema garantiza FORMA, no VERDAD. Un valor inventado en
un campo bien formado lo agarra el ANCLAJE contra OCR (los jueces), no el esquema.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator

# Claves del schema-LLM deben matchear ^[a-zA-Z0-9_.-]{1,64}$ (restricción de Anthropic
# tool input_schema, verificada en vivo). El NOMBRE humano ("base imponible") es para el
# prompt y la traza; la CLAVE de wire es su slug ("base_imponible").
_SLUG_STRIP = re.compile(r"[^A-Za-z0-9_.\-]")


def _slugify(name: str) -> str:
    s = re.sub(r"\s+", "_", name.strip())
    return (_SLUG_STRIP.sub("", s)[:64]) or "campo"


class SemanticType(StrEnum):
    """Tipo semántico de una entidad — VIVE SOLO EN VERA (nunca en el schema del LLM).
    Decide la canonicalización al anclar contra el OCR.
    """
    NUMBER          = "number"           # monto/cifra → magnitud canónica
    CURRENCY_AMOUNT = "currency_amount"  # importe → magnitud canónica + DIVISA (esperada en contrato)
    PERCENTAGE      = "percentage"       # tasa/alícuota → magnitud del número-% (21% ≡ 21,0%)
    ENUM            = "enum"             # valor ∈ conjunto declarado ∧ presente en el doc
    DATE            = "date"             # fecha → normalización de formato
    PERIOD          = "period"           # período (mes-año / trimestre / año) → canónico
    IDENTIFIER      = "identifier"       # IBAN/CUIT/NIF/CIF/expediente/nº → core canónico
    TEXT_ANCHORED   = "text_anchored"    # texto corto → presencia verbatim (tokens) junto a etiqueta
    TEXT            = "text"             # prosa libre → UNVERIFIED (sin anclaje genérico)


@dataclass(frozen=True)
class EntitySpec:
    """Una entidad declarada por el contrato de dominio: NOMBRE + tipo semántico.
    (La CARGA del contrato desde config es el punto 2; acá es la representación en memoria.)

    ``fmt`` (opcional, SOLO para IDENTIFIER): formato conocido del identificador
    ('iban'/'cuit'/'cuil'/'nif'/'cif') → habilita validación de checksum determinista. Vive
    solo en VERA (como el tipo); nunca se le filtra al LLM. None = identificador genérico.
    """
    name: str
    type: SemanticType
    fmt: str | None = None
    currency: str | None = None   # SOLO CURRENCY_AMOUNT: divisa esperada (ISO/símbolo). Solo VERA.
    locale: str | None = None     # SOLO DATE/PERIOD: locale para desambiguar dd/mm (es-AR, en-US). Solo VERA.
    page: int | None = None       # opcional: acota el anclaje a esa página (1-indexed) del OCR. Solo VERA.
    optional: bool = False        # si True, no-confirmable (UNVERIFIED/REVIEW) NO bloquea ok global.
    enum: tuple[str, ...] | None = None   # SOLO ENUM: conjunto de valores permitidos. Solo VERA.
    aliases: tuple[str, ...] = ()         # sinónimos de la etiqueta en el doc (IVA ≡ 'Impuesto…'). Solo VERA.

    @property
    def key(self) -> str:
        """Clave de wire (slug provider-safe) derivada del nombre. Es la clave del
        schema-LLM y del dict de extracción; el nombre humano queda para prompt/traza."""
        return _slugify(self.name)


@dataclass(frozen=True)
class ValidationResult:
    """Resultado determinista de la revalidación de forma."""
    ok: bool
    errors: list[str]


# Bloque de instrucción del fragmento-de-prompt. SOLO forma/nombres; coherente con el
# invariante de Fase 0 (null-explícito, valor como string). NO menciona tipos.
_PROMPT_HEADER = (
    "Extraé del documento los siguientes campos. Devolvé el valor EXACTO tal como "
    "aparece en el texto, como string. Si un campo NO está en el documento, devolvé "
    "null (no lo omitas, no inventes):"
)


def _names(contract: list[EntitySpec]) -> list[str]:
    """NOMBRES humanos declarados (orden del contrato). Para el prompt y la traza."""
    return [e.name for e in contract]


def _keys(contract: list[EntitySpec]) -> list[str]:
    """CLAVES de wire (slugs) — claves del schema-LLM y del dict de extracción."""
    return [e.key for e in contract]


def build_llm_schema(contract: list[EntitySpec]) -> dict[str, Any]:
    """Deriva el JSON Schema canónico SOLO-FORMA para imponer al LLM y revalidar en VERA.

    Object cerrado, todas las CLAVES (slugs provider-safe) required, cada valor string|null.
    NO incluye tipo semántico (estructuralmente imposible que se le filtre al modelo). Orden
    determinista (el del contrato).
    """
    keys = _keys(contract)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "additionalProperties": False,           # cierra inventos de CLAVE (forma)
        "required": keys,                         # null-explícito: ausencia = error
        "properties": {k: {"type": ["string", "null"]} for k in keys},
    }


def build_type_map(contract: list[EntitySpec]) -> dict[str, SemanticType]:
    """Deriva el mapa {nombre: SemanticType} para el ANCLAJE en VERA. SOLO VERA lo usa."""
    return {e.name: e.type for e in contract}


def build_prompt_fragment(contract: list[EntitySpec]) -> str:
    """Deriva el fragmento de prompt que pide las entidades al LLM (3er artefacto, Fase A).

    SOLO forma y NOMBRES (lee `e.name`, nunca `e.type`): lista los campos uno por línea
    (``- {name}``, orden del contrato) con la instrucción de null-explícito. NO menciona
    el tipo semántico ni pistas de canonicalización — el LLM llena valores, VERA decide
    tipo. Contrato vacío → ``""`` (no hay nada que pedir).
    """
    names = _names(contract)
    if not names:
        return ""
    return "\n".join([_PROMPT_HEADER, *(f"- {n}" for n in names)])


def serialize_contract(contract: list[EntitySpec]) -> list[dict[str, Any]]:
    """Serializa el contrato para persistencia/replay: ``[{name, type, fmt}]`` en ORDEN.
    Round-trip con ``load_contract`` (que lee 'type' + 'format'/'fmt')."""
    return [{"name": e.name, "type": e.type.value, "fmt": e.fmt, "currency": e.currency,
             "locale": e.locale, "page": e.page, "optional": e.optional,
             "enum": list(e.enum) if e.enum is not None else None,
             "aliases": list(e.aliases)} for e in contract]


def contract_fingerprint(contract: list[EntitySpec]) -> str:
    """Versión determinista del contrato (hash estable de (key, type, fmt) EN ORDEN). Dos
    contratos idénticos → mismo fingerprint; cualquier cambio (tipo, fmt, orden, entidad) → otro.
    Es el ``contract_version`` que ata un veredicto al contrato exacto que lo produjo (auditoría)."""
    payload = [[e.key, e.type.value, e.fmt, e.currency, e.locale, e.page, e.optional,
                list(e.enum) if e.enum is not None else None, list(e.aliases)]
               for e in contract]
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def validate_output(output: Any, contract: list[EntitySpec]) -> ValidationResult:
    """REVALIDA el output recibido contra el esquema canónico (cinturón y tirantes).

    Determinista, sin LLM. Enforca SIEMPRE additionalProperties:false, todos los campos
    required (null-explícito) y valores string|null — independientemente de lo que el modo
    estructurado del proveedor haya enforced (p.ej. Gemini no enforca additionalProperties).
    """
    schema = build_llm_schema(contract)
    validator = Draft202012Validator(schema)
    errors = sorted(
        f"{'/'.join(str(p) for p in e.absolute_path) or '<root>'}: {e.message}"
        for e in validator.iter_errors(output)
    )
    return ValidationResult(ok=not errors, errors=errors)
