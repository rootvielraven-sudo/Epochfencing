# Autor: @Raven
"""Modelo de ruido OCR realista, parametrizado y logueado (3 tiers).

Se aplica UNIFORMEMENTE a todo el documento — NO se especializa en campos de precisión
(inyectar ruido solo en montos falsearía el resultado). El LLM lee el texto ruidoso; VERA
verifica contra el MISMO texto ruidoso; la verdad son los valores LIMPIOS correctos.

Corrupciones: confusiones de caracter (0<->O, 1<->l<->I, 5<->S, 8<->B, rn<->m), drops/inserts
aleatorios, corrupción de espacios/saltos cerca de números, pérdida de acentos, swaps de dígitos.
"""
from __future__ import annotations

import random
import unicodedata

# Confusiones OCR clásicas (bidireccionales). 'rn'->'m' se maneja aparte (2 chars).
_CONF = {
    "0": "O", "O": "0", "o": "0",
    "1": "l", "l": "1", "I": "1", "i": "1",
    "5": "S", "S": "5", "s": "5",
    "8": "B", "B": "8",
    "2": "Z", "Z": "2",
    "6": "G", "9": "g",
}

# Parámetros por tier (LOGUEADOS en el runner). p_* = probabilidad por caracter/evento.
# Calibrados a OCR PROFESIONAL: los motores buenos (Azure DI, etc.) NO destrozan los datos.
# La mayoría de los documentos son 'clean' (casi perfecto); 'degraded'/'poor' son el
# subconjunto de fotocopias/faxes/cursiva. No se tunea a ningún error objetivo.
NOISE_TIERS = {
    # impreso bueno / PDF-nativo (la mayoría): casi sin errores
    "clean":    {"p_conf": 0.004, "p_drop": 0.0008, "p_ins": 0.0008,
                 "p_space": 0.003, "p_accent": 0.10, "p_digitswap": 0.0008, "p_numsplit": 0.0008},
    # fotocopia / escaneo degradado (subconjunto): errores moderados
    "degraded": {"p_conf": 0.012, "p_drop": 0.003, "p_ins": 0.003,
                 "p_space": 0.008, "p_accent": 0.30, "p_digitswap": 0.003, "p_numsplit": 0.003},
    # fax pobre / cursiva parcial (minoría): peor pero acotado
    "poor":     {"p_conf": 0.025, "p_drop": 0.006, "p_ins": 0.006,
                 "p_space": 0.015, "p_accent": 0.50, "p_digitswap": 0.006, "p_numsplit": 0.006},
}


def _strip_accent(ch: str) -> str:
    d = unicodedata.normalize("NFD", ch)
    base = "".join(c for c in d if unicodedata.category(c) != "Mn")
    return base or ch


def apply_noise(text: str, tier: str, rng: random.Random) -> str:
    """Devuelve el texto con ruido OCR del tier dado. Determinista respecto de ``rng``."""
    p = NOISE_TIERS[tier]
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""

        # rn -> m
        if ch == "r" and nxt == "n" and rng.random() < p["p_conf"]:
            out.append("m"); i += 2; continue
        # m -> rn
        if ch == "m" and rng.random() < p["p_conf"] * 0.5:
            out.append("rn"); i += 1; continue

        # swap de dígitos adyacentes
        if ch.isdigit() and nxt.isdigit() and rng.random() < p["p_digitswap"]:
            out.append(nxt); out.append(ch); i += 2; continue

        # split de número (inserta espacio dentro de una cifra)
        if ch.isdigit() and nxt.isdigit() and rng.random() < p["p_numsplit"]:
            out.append(ch); out.append(" "); i += 1; continue

        # corrupción de espacio cerca de números
        if ch == " ":
            prev = out[-1] if out else ""
            near_num = prev.isdigit() or nxt.isdigit()
            if near_num and rng.random() < p["p_space"]:
                if rng.random() < 0.5:
                    i += 1; continue            # drop del espacio (fusiona tokens)
                else:
                    out.append("\n"); i += 1; continue   # espacio -> salto de línea

        # pérdida de acento
        if unicodedata.category(unicodedata.normalize("NFD", ch)[0]) == "L" and ch != _strip_accent(ch):
            if rng.random() < p["p_accent"]:
                ch = _strip_accent(ch)

        # confusión de caracter
        if ch in _CONF and rng.random() < p["p_conf"]:
            ch = _CONF[ch]

        # drop aleatorio
        if rng.random() < p["p_drop"]:
            i += 1; continue

        out.append(ch)

        # insert aleatorio (duplica o mete un confusable)
        if rng.random() < p["p_ins"]:
            out.append(_CONF.get(ch, ch))

        i += 1

    return "".join(out)
