# Autor: @Raven
"""VERA — clean stage: normalización OCR CONSERVADORA para reconocer números/códigos.

Objetivo: ayudar al anclaje cuando el OCR confunde caracteres DENTRO de valores numéricos
o de código (l.234,56 → 1.234,56 ; 2O-57479675-0 → 20-57479675-0), SIN tocar la prosa
(no cambia "García", "Servico", etc.). Es una ayuda de robustez OPT-IN (Source.ocr(text,
clean=True)); por defecto está apagado.

Regla: solo se normaliza un TOKEN si es "numérico-ish" (tiene ≥1 dígito y la mayoría de sus
alfanuméricos son dígitos o letras-confundibles). Solo mapea letra→dígito en esos tokens.
Nunca fusiona números ni altera valores no numéricos → no puede enmascarar prosa.
"""
from __future__ import annotations

import re

# Letra OCR-confundible → dígito (reverso de las confusiones típicas). Solo en tokens numéricos.
_L2D = {"O": "0", "o": "0", "l": "1", "I": "1", "i": "1",
        "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "G": "6", "g": "9"}
_CONFUSABLE = set(_L2D)


def _is_numeric_ish(tok: str) -> bool:
    """El token es un valor numérico/código con posible ruido OCR (no una palabra)."""
    alnum = [c for c in tok if c.isalnum()]
    if not alnum:
        return False
    digits = sum(c.isdigit() for c in alnum)
    if digits == 0:
        return False
    digit_or_conf = digits + sum(1 for c in alnum if c in _CONFUSABLE)
    return digit_or_conf / len(alnum) >= 0.6


def _fix_token(tok: str) -> str:
    return "".join(_L2D.get(c, c) for c in tok)


def clean_ocr_text(text: str) -> str:
    """Normaliza confusiones OCR dentro de tokens numéricos/código. Preserva espacios y prosa."""
    parts = re.split(r"(\s+)", text)
    out = []
    for p in parts:
        out.append(_fix_token(p) if p and not p.isspace() and _is_numeric_ish(p) else p)
    return "".join(out)
