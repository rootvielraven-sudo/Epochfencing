# Autor: @Raven
"""Inyección CONTROLADA de fallos. Como los inyectamos nosotros, conocemos la verdad de
referencia sin anotación manual.

Dos familias:

  CONTENIDO (sobre la extracción reconstruida correctamente):
    - ``silent_corruption``: altera un número de modo que ROMPE un invariante aritmético
      (p.ej. el total deja de ser subtotal+IVA). El valor "existe" pero no cuadra →
      lo caza la dimensión de CÁLCULO de VERA.
    - ``hallucination``: reemplaza un valor por otro que NO aparece en el OCR (importe/
      fecha/CUIT inventados) → sin anclaje → lo caza la dimensión de COBERTURA (INVENTED).

  INFRAESTRUCTURA:
    - ``CrashPlan``: decide, de forma determinista, qué (invoice_id, intento) debe morir a
      mitad de proceso, para simular caídas de nodo sin matar procesos de verdad.

Cada inyección de contenido devuelve ``(extraction_corrupta, label)`` donde ``label`` es la
etiqueta de verdad: 'clean' | 'corruption' | 'hallucination'.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from typing import Any

from framework.vera.calc_contract import amount_to_cents

_AMOUNT_FIELDS = ["Total a pagar", "Importe IVA", "Subtotal"]
_CALC_MIRROR = {"Total a pagar": "total", "Importe IVA": "importe_iva", "Subtotal": "subtotal"}


def _fmt_cents(cents: int) -> str:
    neg = cents < 0
    cents = abs(cents)
    pesos, cc = divmod(cents, 100)
    return f"{'-' if neg else ''}$ {pesos:,}".replace(",", ".") + f",{cc:02d}"


# ── CORRUPCIÓN SILENCIOSA (rompe invariante de cálculo) ──────────────────────
def silent_corruption(extraction: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Altera el TOTAL (y su espejo de cálculo) en un delta que rompe subtotal+IVA=total,
    pero deja la factura bien formada. No toca el OCR → el valor sigue sin estar, pero el
    objetivo es que lo DETECTE la dimensión de cálculo (inconsistencia aritmética)."""
    e = copy.deepcopy(extraction)
    cur = amount_to_cents(e.get("total"))
    if cur is None:
        return e
    delta = rng.choice([-1, 1]) * rng.randint(100_00, 500_00)   # ±100,00 .. 500,00
    bad = int(cur) + delta
    e["total"] = _fmt_cents(bad)
    e["Total a pagar"] = _fmt_cents(bad)
    return e


# ── ALUCINACIÓN (valor fabricado, sin base en el OCR) ────────────────────────
def hallucination(extraction: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Reemplaza un campo por un valor inventado que no aparece en la fuente.

    Elige entre importe, fecha o CUIT. El valor es plausible pero fabricado (el sello de la
    alucinación del LLM: completa la tarea limpiamente con un dato que no está)."""
    e = copy.deepcopy(extraction)
    kind = rng.choice(["amount", "date", "cuit"])
    if kind == "amount":
        cur = amount_to_cents(e.get("total")) or 100_000_00
        fake = int(cur) + rng.randint(10_000_00, 90_000_00)      # salto grande, no ambiguo
        e["total"] = _fmt_cents(fake)
        e["Total a pagar"] = _fmt_cents(fake)
    elif kind == "date":
        e["Fecha de emisión"] = f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2025"
    else:  # cuit inventado
        e["CUIT emisor"] = f"30-{rng.randint(10_000_000, 99_999_999)}-{rng.randint(0,9)}"
    return e


# ── CAÍDA DE NODO (plan determinista de qué intento muere) ───────────────────
@dataclass
class CrashPlan:
    """Decide qué (invoice_id, intento) debe fallar a mitad de proceso.

    ``crash_fraction`` de las facturas sufre una caída en su PRIMER intento (``crash_attempt``).
    Determinista por ``seed`` → reproducible. Lleva la cuenta de cuántas caídas se dispararon.
    """
    invoice_ids: list[str]
    crash_fraction: float = 0.3
    crash_attempt: int = 1
    seed: int = 7
    _to_crash: set[str] = field(default_factory=set)
    crashes_fired: int = 0

    def __post_init__(self):
        rng = random.Random(self.seed)
        k = int(round(len(self.invoice_ids) * self.crash_fraction))
        self._to_crash = set(rng.sample(self.invoice_ids, k)) if k else set()

    def should_crash(self, invoice_id: str, attempt: int) -> bool:
        return invoice_id in self._to_crash and attempt == self.crash_attempt

    @property
    def planned(self) -> int:
        return len(self._to_crash)


class InjectedCrash(Exception):
    """Un worker 'cae' a mitad de proceso (simula caída de nodo)."""


# ── PLAN de fallos de CONTENIDO (etiqueta de verdad por factura) ─────────────
@dataclass
class ContentFaultPlan:
    """Asigna a cada factura una etiqueta: 'clean' | 'corruption' | 'hallucination'.

    Determinista por ``seed``. ``corruption_rate`` y ``hallucination_rate`` son fracciones
    del total; el resto queda limpio. Expone un ``hook(invoice_id, extraction, rng)`` para el
    reconstructor y guarda la verdad de referencia (``label``) para medir detección/FP/FN.
    """
    invoice_ids: list[str]
    corruption_rate: float = 0.25
    hallucination_rate: float = 0.25
    seed: int = 20261112
    calc_ids: set[str] | None = None     # facturas aptas para corrupción de cálculo (con líneas
                                         # Σ=total). Si se da, la corrupción solo va a estas.
    label: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        rng = random.Random(self.seed)
        ids = list(self.invoice_ids)
        rng.shuffle(ids)
        n = len(ids)
        n_cor = int(round(n * self.corruption_rate))
        n_hal = int(round(n * self.hallucination_rate))
        # Candidatas a corrupción: con cálculo si se restringe; si no, cualquiera.
        calc_pool = [i for i in ids if (self.calc_ids is None or i in self.calc_ids)]
        cor_set = set(calc_pool[:n_cor])
        rest = [i for i in ids if i not in cor_set]
        hal_set = set(rest[:n_hal])
        for iid in ids:
            if iid in cor_set:
                self.label[iid] = "corruption"
            elif iid in hal_set:
                self.label[iid] = "hallucination"
            else:
                self.label[iid] = "clean"

    def hook(self, invoice_id: str, extraction: dict[str, Any],
             rng: random.Random) -> dict[str, Any]:
        lab = self.label.get(invoice_id, "clean")
        if lab == "corruption":
            return silent_corruption(extraction, rng)
        if lab == "hallucination":
            return hallucination(extraction, rng)
        return extraction

    @property
    def counts(self) -> dict[str, int]:
        c = {"clean": 0, "corruption": 0, "hallucination": 0}
        for v in self.label.values():
            c[v] += 1
        return c
