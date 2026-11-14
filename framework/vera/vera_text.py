# Autor: @Raven
"""VERA — anclaje de TEXTO corto (TEXT_ANCHORED, P2). Determinista, sin LLM.

Confirma por PRESENCIA VERBATIM a nivel de tokens (insensible a case / acentos / puntuación):
el valor del LLM ancla SUPPORTED si su secuencia de tokens aparece CONTIGUA en el texto dado.

    presente (≥ _MIN chars) → SUPPORTED   ·   ausente o trivial → REVIEW

NUNCA INVENTED: un texto reformulado ("ACME S.A." vs "ACME SA") no es una alucinación demostrable
→ no se acusa, va a revisión. Rol/proximidad y null los maneja match_coverage. Es estricto a
propósito (cero falsos SUPPORTED): una variante no-verbatim queda en REVIEW, no en SUPPORTED.
"""
from __future__ import annotations

import re

from .vera_types import EntityVerdict

_MIN_TEXT_CHARS = 4   # piso de caracteres alfanuméricos para confirmar (evita 'SA', 'n/a', …)
_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")


def _tokens(s: str) -> list[str]:
    """Tokens alfanuméricos normalizados (minúsculas, sin acentos). 'ACME S.A.' → ['acme','s','a']."""
    return re.findall(r"[a-z0-9]+", (s or "").lower().translate(_ACCENTS))


def _is_contiguous_sublist(sub: list[str], full: list[str]) -> bool:
    m = len(sub)
    if m == 0:
        return False
    return any(full[i:i + m] == sub for i in range(len(full) - m + 1))


def anchor_text(llm_value: str, ocr: str) -> EntityVerdict:
    """Veredicto de cobertura para una entidad TEXT_ANCHORED contra el texto dado.

    SUPPORTED si los tokens del valor aparecen CONTIGUOS en el texto (verbatim normalizado);
    REVIEW si no aparecen o si el valor es demasiado corto/trivial para confirmar. Nunca INVENTED.
    """
    toks = _tokens(llm_value)
    if sum(len(t) for t in toks) < _MIN_TEXT_CHARS:
        return EntityVerdict.REVIEW          # demasiado corto/trivial → no confirmamos
    return (EntityVerdict.SUPPORTED if _is_contiguous_sublist(toks, _tokens(ocr))
            else EntityVerdict.REVIEW)


def anchor_enum(llm_value: str, ocr: str, options: tuple[str, ...]) -> EntityVerdict:
    """Veredicto de cobertura para una entidad ENUM contra el texto dado.

    SUPPORTED si el valor es una de las ``options`` declaradas (normalizado por tokens) Y aparece
    presente en el texto (sublista contigua). REVIEW si está fuera del conjunto, o si es una opción
    válida pero NO está groundeada en el texto. Nunca INVENTED (no se acusa). Sin piso de chars:
    el conjunto declarado ya acota (admite opciones cortas como 'si'/'no')."""
    toks = _tokens(llm_value)
    if not any(toks == _tokens(o) for o in options):
        return EntityVerdict.REVIEW          # fuera del conjunto declarado → no confirmamos
    return (EntityVerdict.SUPPORTED if _is_contiguous_sublist(toks, _tokens(ocr))
            else EntityVerdict.REVIEW)
