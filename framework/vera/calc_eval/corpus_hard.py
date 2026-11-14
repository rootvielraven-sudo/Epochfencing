# Autor: @Raven
"""Corpus DURO: extracciones realistas (strings es-AR con ruido de OCR) que pasan por
el parser real (``verify_calc``), multi-alícuota (21% + 10,5%), facturas grandes y
fallos difíciles. Mide robustez de detección Y de NO-falsos-positivos bajo ruido.

A diferencia de ``corpus.py`` (construye ``Invoice`` con Fractions directo), acá se
generan DICTS de extracción con valores string y el role-tagging + parseo es parte de
la prueba. RNG sembrado → reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from fractions import Fraction
from typing import List

from ..calc_contract import CalcContract, CalcLineMap, CalcTaxMap

RATE21 = Fraction(21, 100)
RATE105 = Fraction(105, 1000)  # 10,5%


@dataclass
class HardCase:
    extraction: dict
    contract: CalcContract
    is_faulty: bool
    detectable: bool
    fault_class: str
    note: str = ""


_CONTRACT = CalcContract(
    lines=CalcLineMap(array="items", qty="cantidad", price="precio_unitario", amount="importe"),
    subtotal="subtotal",
    total="total",
    taxes=(
        CalcTaxMap(base="base_21", rate="alic_21", amount="iva_21", tax_id="iva21"),
        CalcTaxMap(base="base_105", rate="alic_105", amount="iva_105", tax_id="iva105"),
    ),
)


# ───────────────────────────── formato con ruido de OCR ────────────────────

def _fmt_ar(cents: int, rng: random.Random) -> str:
    """Formatea céntimos a un string es-AR VÁLIDO con ruido (símbolo/espacios/separadores).
    Todos los estilos parsean a la MISMA magnitud → sirven de estrés de falsos positivos."""
    pesos, cc = divmod(abs(cents), 100)
    miles = f"{pesos:,}".replace(",", ".")   # 59800 -> "59.800"
    body_dec = f"{miles},{cc:02d}"           # "59.800,00"
    style = rng.randint(0, 6)
    s = {
        0: body_dec,
        1: f"$ {body_dec}",
        2: f"${body_dec}",
        3: f"ARS {body_dec}",
        4: f"{pesos:,}".replace(",", ".") if cc == 0 else body_dec,  # sin decimales si enteros
        5: f"  {body_dec}  ",                # espacios
        6: f"{pesos},{cc:02d}" if pesos < 1000 else body_dec,  # sin sep de miles
    }[style]
    return ("-" + s) if cents < 0 else s


def _round_half_up(frac: Fraction) -> int:
    return int(frac + Fraction(1, 2)) if frac >= 0 else -int(-frac + Fraction(1, 2))


# ───────────────────────────── generación base ─────────────────────────────

def _base_invoice(rng: random.Random, n_lines: int) -> dict:
    """Factura CONSISTENTE (céntimos enteros), multi-alícuota. Primeras mitad de líneas
    a 21%, resto a 10,5%. Devuelve dict con céntimos (para inyectar fallos antes de formatear)."""
    lines = []
    for _ in range(n_lines):
        qty = rng.randint(1, 12)
        price_c = rng.randint(5000, 2_000_000)  # céntimos
        amount_c = qty * price_c
        lines.append({"qty": qty, "price_c": price_c, "amount_c": amount_c})
    k = max(1, n_lines // 2)
    base21_c = sum(l["amount_c"] for l in lines[:k])
    base105_c = sum(l["amount_c"] for l in lines[k:])
    iva21_c = _round_half_up(Fraction(base21_c) * RATE21)
    iva105_c = _round_half_up(Fraction(base105_c) * RATE105)
    subtotal_c = base21_c + base105_c
    total_c = base21_c + iva21_c + base105_c + iva105_c
    return {"lines": lines, "base21_c": base21_c, "base105_c": base105_c,
            "iva21_c": iva21_c, "iva105_c": iva105_c,
            "subtotal_c": subtotal_c, "total_c": total_c}


def _to_extraction(t: dict, rng: random.Random) -> dict:
    """Factura numérica (céntimos) → dict de extracción con strings ruidosos."""
    items = [{"cantidad": str(l["qty"]),
              "precio_unitario": _fmt_ar(l["price_c"], rng),
              "importe": _fmt_ar(l["amount_c"], rng)} for l in t["lines"]]
    return {
        "items": items,
        "subtotal": _fmt_ar(t["subtotal_c"], rng),
        "base_21": _fmt_ar(t["base21_c"], rng), "alic_21": rng.choice(["21%", "21", "21,00%"]),
        "iva_21": _fmt_ar(t["iva21_c"], rng),
        "base_105": _fmt_ar(t["base105_c"], rng), "alic_105": rng.choice(["10,5%", "10.5%", "10,50%"]),
        "iva_105": _fmt_ar(t["iva105_c"], rng),
        "total": _fmt_ar(t["total_c"], rng),
    }


# ───────────────────────────── fallos difíciles ────────────────────────────

def make_hard_case(fault_class: str, rng: random.Random) -> HardCase:
    n = rng.randint(4, 12)
    t = _base_invoice(rng, n)

    def case(faulty, detectable, note):
        return HardCase(_to_extraction(t, rng), _CONTRACT, faulty, detectable, fault_class, note)

    if fault_class == "clean_noisy":
        return case(False, False, "coherente con ruido de formato OCR")

    if fault_class == "rounding_legit":
        # cuota con 1 céntimo de desvío por redondeo → dentro de ε, NO es fallo
        t["iva21_c"] += 1
        t["total_c"] += 1
        return case(False, False, "redondeo legítimo (±1c): no debe rechazarse")

    if fault_class == "digit_transposition_total":
        # transpone el primer par de dígitos ADYACENTES que difieran (garantiza cambio real)
        s = list(str(t["total_c"]))
        for i in range(len(s) - 1):
            if s[i] != s[i + 1]:
                s[i], s[i + 1] = s[i + 1], s[i]
                break
        t["total_c"] = int("".join(s))
        return case(True, True, "dígitos transpuestos en el total")

    if fault_class == "dropped_digit_line":
        t["lines"][0]["amount_c"] //= 10  # se perdió un dígito del importe
        return case(True, True, "dígito perdido en importe de línea")

    if fault_class == "iva_rate_crossed":
        # la cuota 21% se calculó pero el documento declara 10,5% para esa base
        # → base*rate(10,5) != cuota(21).  Lo simulamos poniendo iva_21 = base*21% ok,
        # pero cruzando: ponemos en iva_21 el valor de 10,5%.
        t["iva21_c"] = _round_half_up(Fraction(t["base21_c"]) * RATE105)
        t["total_c"] = t["base21_c"] + t["iva21_c"] + t["base105_c"] + t["iva105_c"]
        # total recomputado cierra; el que NO cierra es base21*21% == iva_21  (rate declarada 21%)
        return case(True, True, "alícuota cruzada (cuota de 10,5% con base al 21%)")

    if fault_class == "rows_merged_changes_sum":
        # dos filas colapsan en una cuyo importe NO es la suma → Σlíneas != subtotal
        if n >= 2:
            t["lines"][0]["amount_c"] = t["lines"][0]["amount_c"] + 777  # merge mal
        return case(True, True, "filas fusionadas (Σlíneas != subtotal)")

    if fault_class == "subtotal_off":
        t["subtotal_c"] += rng.randint(100, 9000)
        return case(True, True, "subtotal no cierra con Σlíneas")

    if fault_class == "total_off_big":
        t["total_c"] += rng.randint(5000, 50000)
        return case(True, True, "total inflado")

    if fault_class == "iva_wrong":
        t["iva105_c"] += rng.randint(300, 5000)
        return case(True, True, "cuota 10,5% mal")

    if fault_class == "off_by_one_cent":
        # UN céntimo de más en el total: dentro de ε → NO detectable (zona de redondeo)
        t["total_c"] += 1
        return case(False, False, "±1c en total: zona de tolerancia (no es fallo real)")

    if fault_class == "satisfies_invariants":
        # qty y precio cambian MANTENIENDO el importe (q*p) y todo aguas arriba → ciego.
        # Se elige una línea donde la refactorización mantenga enteros (qty par: /2 y *2;
        # o precio par: *2 y /2). El importe NO cambia → subtotal/total siguen cerrando.
        for li in t["lines"]:
            if li["qty"] % 2 == 0:
                li["qty"] //= 2
                li["price_c"] *= 2
                break
            if li["price_c"] % 2 == 0:
                li["qty"] *= 2
                li["price_c"] //= 2
                break
        return case(True, False, "error que satisface invariantes (ciego declarado)")

    raise ValueError(f"fault_class duro desconocido: {fault_class}")


HARD_CLASSES = [
    "clean_noisy", "rounding_legit", "digit_transposition_total", "dropped_digit_line",
    "iva_rate_crossed", "rows_merged_changes_sum", "subtotal_off", "total_off_big",
    "iva_wrong", "off_by_one_cent", "satisfies_invariants",
]


def build_hard_corpus(per_class: int = 40, seed: int = 11) -> List[HardCase]:
    rng = random.Random(seed)
    cases: List[HardCase] = []
    for fc in HARD_CLASSES:
        for _ in range(per_class):
            cases.append(make_hard_case(fc, rng))
    return cases


__all__ = ["HardCase", "make_hard_case", "build_hard_corpus", "HARD_CLASSES"]
