# Autor: @Raven
"""VERA — routing doc→contrato (determinista, sin LLM).

Clasifica un documento por su CONTENIDO (etiquetas/encabezados distintivos) y elige el tipo de
documento → su contrato de cobertura. CONSERVADOR: si la clasificación no tiene un ganador CLARO
(señales suficientes Y estrictamente por encima del segundo), devuelve None → NO rutea (no adivina
un contrato equivocado, que daría REVIEW/UNVERIFIED ruidoso sobre un doc que no es de ese tipo).

Reproducible: matching por subsecuencia de tokens normalizados (insensible a case/acentos/puntuación).
El LLM no participa de la clasificación que decide el contrato.
"""
from __future__ import annotations

import re

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")
_MIN_SIGNALS = 2   # piso de señales para rutear (evita clasificar por ruido)


def _tokens(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower().translate(_ACCENTS))


def _has_phrase(doc_tokens: list[str], phrase_tokens: list[str]) -> bool:
    m = len(phrase_tokens)
    if m == 0:
        return False
    return any(doc_tokens[i:i + m] == phrase_tokens for i in range(len(doc_tokens) - m + 1))


def _score(ocr_tokens: list[str], signals: list[str]) -> int:
    return sum(1 for phrase in signals if _has_phrase(ocr_tokens, _tokens(phrase)))


def classify_document(ocr: str, signals: dict[str, list[str]]) -> str | None:
    """Devuelve el nombre del tipo de documento con MÁS señales presentes, si es un ganador CLARO
    (score ≥ _MIN_SIGNALS y estrictamente mayor que el segundo). None si ambiguo o sin señales."""
    toks = _tokens(ocr)
    ranked = sorted(((name, _score(toks, sg)) for name, sg in signals.items()),
                    key=lambda x: x[1], reverse=True)
    if not ranked or ranked[0][1] < _MIN_SIGNALS:
        return None
    if len(ranked) > 1 and ranked[1][1] == ranked[0][1]:
        return None          # empate → ambiguo → no rutea
    return ranked[0][0]
