# Autor: @Raven
"""Corpus sintético de facturas con fallos inyectados (§9 del paper) + ground truth.

Cada caso es ``Case(invoice, is_faulty, detectable, fault_class, note)``:
  - ``is_faulty``   : la factura tiene un error real (verdad de terreno).
  - ``detectable``  : la condición B DEBERÍA detectarlo (UNSAT alcanzable). El caso
                      "satisface invariantes" es faulty pero NO detectable (límite).
  - ``fault_class`` : clase de fallo (para el desglose por clase).

Construcción honesta: la "verdad" es internamente consistente (amount=qty·precio,
base=Σamount-desc, cuota=base·tasa redondeada, total=base+cuota). El fallo cambia
UNA (o varias) observación(es) dejando el resto como el valor impreso → genera la
inconsistencia relacional que la verificación campo-a-campo no ve.

Todo con RNG sembrado → corpus reproducible.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from fractions import Fraction
from typing import List, Optional

from ..calc_consistency import (
    Invoice, LineItem, Obs, Role, TaxComponent, to_cents, to_fraction,
)

RATE = Fraction(21, 100)  # IVA 21%


@dataclass
class Case:
    invoice: Invoice
    is_faulty: bool
    detectable: bool
    fault_class: str
    note: str = ""


# ───────────────────────────── construcción ────────────────────────────────

def _round_cents(c: Fraction) -> Fraction:
    """Redondea céntimos-fraccionarios al céntimo entero más cercano (half-up)."""
    return Fraction(int(c + Fraction(1, 2))) if c >= 0 else Fraction(-int(-c + Fraction(1, 2)))


def _truth(rng: random.Random, n_lines: int = 3, with_discount: bool = False) -> dict:
    """Factura numérica CONSISTENTE (en pesos exactos como Fraction)."""
    lines = []
    for _ in range(n_lines):
        qty = Fraction(rng.randint(1, 9))
        price = Fraction(rng.randint(100, 5000), 100)  # pesos con 2 decimales exactos
        amount = qty * price
        lines.append({"qty": qty, "price": price, "amount": amount})
    discount = Fraction(rng.randint(100, 800), 100) if with_discount else None
    base = sum((l["amount"] for l in lines), Fraction(0))
    if discount:
        base -= discount
    tax = _round_cents(to_cents(base) * RATE) / 100  # cuota redondeada a céntimo, en pesos
    total = base + tax
    return {"lines": lines, "discount": discount, "base": base, "tax": tax, "total": total}


def _to_invoice(t: dict, *, drop_discount: bool = False) -> Invoice:
    """Convierte la factura numérica en observaciones role-tagged (Invoice)."""
    lines = []
    for i, l in enumerate(t["lines"]):
        lines.append(LineItem(
            line_id=str(i),
            qty=Obs(f"L{i}.qty", Role.QTY, to_fraction(l["qty"])),
            unit_price=Obs(f"L{i}.price", Role.UNIT_PRICE, to_cents(l["price"])),
            amount=Obs(f"L{i}.amount", Role.LINE_AMOUNT, to_cents(l["amount"])),
        ))
    disc = None
    if t["discount"] is not None and not drop_discount:
        disc = Obs("discount", Role.DISCOUNT, to_cents(t["discount"]))
    tax = TaxComponent(
        tax_id="iva",
        base=Obs("tax.base", Role.TAX_BASE, to_cents(t["base"])),
        rate=Obs("tax.rate", Role.TAX_RATE, to_fraction(RATE)),
        amount=Obs("tax.amount", Role.TAX_AMOUNT, to_cents(t["tax"])),
    )
    return Invoice(
        lines=lines,
        subtotal=Obs("subtotal", Role.SUBTOTAL, to_cents(t["base"])),
        discount=disc,
        taxes=[tax],
        total=Obs("total", Role.TOTAL, to_cents(t["total"])),
    )


# ───────────────────────────── inyección de fallos ─────────────────────────

def make_case(fault_class: str, rng: random.Random) -> Case:
    if fault_class == "clean":
        return Case(_to_invoice(_truth(rng)), False, False, "clean", "sin fallo")

    if fault_class == "qty_wrong":
        t = _truth(rng)
        t["lines"][0]["qty"] += 1  # qty extraída mal; amount queda como el impreso
        return Case(_to_invoice(t), True, True, "qty_wrong", "qty != amount/precio")

    if fault_class == "price_wrong":
        t = _truth(rng)
        t["lines"][0]["price"] += Fraction(1000, 100)
        return Case(_to_invoice(t), True, True, "price_wrong", "precio != amount/qty")

    if fault_class == "amount_wrong":
        t = _truth(rng)
        t["lines"][0]["amount"] += Fraction(3333, 100)
        return Case(_to_invoice(t), True, True, "amount_wrong", "amount != qty*precio")

    if fault_class == "row_omitted":
        t = _truth(rng, n_lines=4)
        inv = _to_invoice(t)
        inv.lines.pop()  # el LLM se saltó una fila; base/total impresos la incluyen
        return Case(inv, True, True, "row_omitted", "Σamount < subtotal")

    if fault_class == "row_duplicated":
        t = _truth(rng, n_lines=3)
        inv = _to_invoice(t)
        dup = inv.lines[0]
        inv.lines.append(LineItem(
            line_id="dup",
            qty=Obs("DUP.qty", Role.QTY, dup.qty.value),
            unit_price=Obs("DUP.price", Role.UNIT_PRICE, dup.unit_price.value),
            amount=Obs("DUP.amount", Role.LINE_AMOUNT, dup.amount.value),
        ))
        return Case(inv, True, True, "row_duplicated", "Σamount > subtotal (fila repetida)")

    if fault_class == "misalignment":
        t = _truth(rng)
        # asegurar qty != 1 para que el swap precio<->amount rompa q*p=a
        t["lines"][0]["qty"] = Fraction(rng.randint(2, 9))
        t["lines"][0]["amount"] = t["lines"][0]["qty"] * t["lines"][0]["price"]
        inv = _to_invoice(t)
        li = inv.lines[0]
        li.unit_price.value, li.amount.value = li.amount.value, li.unit_price.value
        return Case(inv, True, True, "misalignment", "columnas precio<->importe cruzadas")

    if fault_class == "subtotal_wrong":
        t = _truth(rng)
        inv = _to_invoice(t)
        inv.subtotal.value += to_cents(Fraction(5000, 100))
        return Case(inv, True, True, "subtotal_wrong", "subtotal != Σamount")

    if fault_class == "iva_wrong":
        t = _truth(rng)
        inv = _to_invoice(t)
        inv.taxes[0].amount.value += to_cents(Fraction(2500, 100))
        return Case(inv, True, True, "iva_wrong", "cuota != base*tasa")

    if fault_class == "discount_omitted":
        t = _truth(rng, with_discount=True)
        inv = _to_invoice(t, drop_discount=True)  # subtotal/total impresos CON descuento
        return Case(inv, True, True, "discount_omitted", "Σamount - (desc omitido) != subtotal")

    if fault_class == "rounding_legit":
        # precios que fuerzan redondeo de céntimo en la cuota; todo coherente dentro de ε.
        t = _truth(rng)
        return Case(_to_invoice(t), False, False, "rounding_legit",
                    "redondeo legítimo: NO debe rechazarse")

    if fault_class == "multiple":
        t = _truth(rng)
        t["lines"][0]["qty"] += 2
        inv = _to_invoice(t)
        inv.taxes[0].amount.value += to_cents(Fraction(1700, 100))
        return Case(inv, True, True, "multiple", "qty + IVA mal simultáneos")

    if fault_class == "satisfies_invariants":
        # qty y precio cambian pero el producto (y todo aguas arriba) se mantiene:
        # 2*50 == 4*25. Internamente consistente -> NO detectable (límite declarado).
        t = _truth(rng)
        q = t["lines"][0]["qty"]
        p = t["lines"][0]["price"]
        t["lines"][0]["qty"] = q * 2
        t["lines"][0]["price"] = p / 2
        # amount, base, tax, total no cambian -> sigue consistente
        return Case(_to_invoice(t), True, False, "satisfies_invariants",
                    "error que satisface invariantes: límite (FN esperado)")

    raise ValueError(f"fault_class desconocida: {fault_class}")


FAULT_CLASSES = [
    "clean", "qty_wrong", "price_wrong", "amount_wrong", "row_omitted",
    "row_duplicated", "misalignment", "subtotal_wrong", "iva_wrong",
    "discount_omitted", "rounding_legit", "multiple", "satisfies_invariants",
]


def build_corpus(per_class: int = 20, seed: int = 7) -> List[Case]:
    """Genera ``per_class`` casos por clase de fallo (corpus reproducible)."""
    rng = random.Random(seed)
    cases: List[Case] = []
    for fc in FAULT_CLASSES:
        for _ in range(per_class):
            cases.append(make_case(fc, rng))
    return cases


__all__ = ["Case", "make_case", "build_corpus", "FAULT_CLASSES", "RATE"]
