# Autor: @Raven
"""Corpus de ESTRÉS EXTREMO: formato US (``1,234.56``) y corrupción de dígitos a
nivel carácter (OCR que rompe dígitos: 5→S, 0→O, 8→B, o dígito→dígito).

Dos propiedades de seguridad bajo prueba:
  1. Formato US: el mismo valor en convención US no debe generar FALSOS POSITIVOS
     (el canonicalizador de VERA desambigua ``1.234,56`` ≡ ``1,234.56``).
  2. Corrupción de OCR: una factura con un dígito corrupto NUNCA debe pasar como
     COHERENTE (aceptada). Debe quedar INCONSISTENT (cazada) o INCOMPLETE (no
     verificable) — el resultado PELIGROSO es COHERENT con datos corruptos.

RNG sembrado → reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List

from .corpus_hard import _CONTRACT, _base_invoice

_CONFUSABLE = {"0": "O", "1": "l", "2": "Z", "5": "S", "6": "G", "8": "B", "9": "g"}


@dataclass
class StressCase:
    extraction: dict
    contract: object
    is_faulty: bool
    fault_class: str
    note: str = ""


def _fmt_us(cents: int, rng: random.Random) -> str:
    """Céntimos → string formato US (coma miles, punto decimales) con ruido."""
    pesos, cc = divmod(abs(cents), 100)
    body = f"{pesos:,}.{cc:02d}"              # "59,800.00"
    style = rng.randint(0, 3)
    s = {0: body, 1: f"$ {body}", 2: f"${body}", 3: f"USD {body}"}[style]
    return ("-" + s) if cents < 0 else s


def _to_extraction_us(t: dict, rng: random.Random) -> dict:
    f = lambda c: _fmt_us(c, rng)
    items = [{"cantidad": str(l["qty"]), "precio_unitario": f(l["price_c"]),
              "importe": f(l["amount_c"])} for l in t["lines"]]
    return {
        "items": items,
        "subtotal": f(t["subtotal_c"]),
        "base_21": f(t["base21_c"]), "alic_21": "21%", "iva_21": f(t["iva21_c"]),
        "base_105": f(t["base105_c"]), "alic_105": "10.5%", "iva_105": f(t["iva105_c"]),
        "total": f(t["total_c"]),
    }


def _corrupt_first_int_digit(s: str, repl) -> str:
    """Reemplaza el PRIMER dígito de la parte entera (cambio de alto orden → > tolerancia).
    ``repl`` recibe el dígito y devuelve el carácter corrupto."""
    out = list(s)
    for i, ch in enumerate(out):
        if ch.isdigit():
            out[i] = repl(ch)
            break
    return "".join(out)


def make_stress_case(fault_class: str, rng: random.Random) -> StressCase:
    t = _base_invoice(rng, rng.randint(4, 10))

    if fault_class == "us_clean":
        return StressCase(_to_extraction_us(t, rng), _CONTRACT, False, "us_clean",
                          "coherente en formato US: no debe dar FP")

    if fault_class == "us_total_wrong":
        t["total_c"] += rng.randint(5000, 50000)
        return StressCase(_to_extraction_us(t, rng), _CONTRACT, True, "us_total_wrong",
                          "total mal en formato US: debe detectarse")

    e = _to_extraction_us(t, rng)

    if fault_class == "ocr_digit_to_digit":
        # OCR confunde un dígito por OTRO dígito en el importe de una línea.
        def swap(d):
            other = str((int(d) + rng.randint(1, 8)) % 10)
            return other if other != d else "0"
        e["items"][0]["importe"] = _corrupt_first_int_digit(e["items"][0]["importe"], swap)
        return StressCase(e, _CONTRACT, True, "ocr_digit_to_digit",
                          "dígito→dígito en importe de línea (valor cambia)")

    if fault_class == "ocr_digit_to_letter":
        # OCR confunde un dígito por una LETRA confundible (5→S, 0→O, 8→B...).
        e["items"][0]["importe"] = _corrupt_first_int_digit(
            e["items"][0]["importe"], lambda d: _CONFUSABLE.get(d, "X"))
        return StressCase(e, _CONTRACT, True, "ocr_digit_to_letter",
                          "dígito→letra en importe (parseo parcial → valor mal)")

    if fault_class == "ocr_unparseable":
        # importe completamente ilegible (todo letras).
        e["items"][0]["importe"] = "".join(_CONFUSABLE.get(c, c) if c.isdigit() else c
                                           for c in e["items"][0]["importe"])
        # fuerza que NINGÚN dígito sobreviva
        e["items"][0]["importe"] = "".join("X" if c.isdigit() else c
                                           for c in e["items"][0]["importe"])
        return StressCase(e, _CONTRACT, True, "ocr_unparseable",
                          "importe ilegible (sin dígitos) → debe quedar INCOMPLETE, no COHERENT")

    raise ValueError(f"fault_class de estrés desconocida: {fault_class}")


STRESS_CLASSES = [
    "us_clean", "us_total_wrong", "ocr_digit_to_digit",
    "ocr_digit_to_letter", "ocr_unparseable",
]


def build_stress_corpus(per_class: int = 60, seed: int = 23) -> List[StressCase]:
    rng = random.Random(seed)
    cases: List[StressCase] = []
    for fc in STRESS_CLASSES:
        for _ in range(per_class):
            cases.append(make_stress_case(fc, rng))
    return cases


__all__ = ["StressCase", "make_stress_case", "build_stress_corpus", "STRESS_CLASSES"]
