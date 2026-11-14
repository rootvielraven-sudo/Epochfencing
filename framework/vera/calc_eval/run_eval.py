# Autor: @Raven
"""Evaluación de la dimensión de consistencia de cálculo (condición B) + ablaciones.

Compara (del §9 del paper, lo que es ejecutable HOY sin solver):
  A. Línea base campo-a-campo (aproximación): sin chequeo relacional.
  B. Verificación determinista de invariantes (este MVP).

Ablaciones:
  - Tolerancia ε ∈ {0, 1, 2} céntimos (mide falsos positivos por redondeo legítimo).
  - Subconjunto de invariantes: solo-líneas / +subtotal / completo (mide qué
    invariante atrapa qué clase de fallo; p.ej. fila omitida/duplicada solo la ve
    el invariante de subtotal, no el de línea).

NO evaluado aquí (fase 2, requiere Z3 que no está en el venv): MUS/MCS mínimos,
QuickXplain, atribución de origen.

Correr:  .venv/bin/python -m framework.vera.calc_eval.run_eval
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable, List

from ..calc_consistency import CalcState, Invoice, check_calc_consistency
from .corpus import Case, FAULT_CLASSES, build_corpus


# ───────────────────────────── condiciones ─────────────────────────────────

def baseline_field_check(inv: Invoice) -> bool:
    """Condición A (aproximación de la verificación campo-a-campo actual): cada
    número está presente y es no-negativo. NO mira relaciones entre campos → por
    construcción no detecta inconsistencias aritméticas relacionales. Devuelve
    ``True`` = "coherente a nivel campo" (nunca marca error relacional)."""
    nums = []
    for li in inv.lines:
        nums += [o for o in (li.qty, li.unit_price, li.amount) if o]
    for tc in inv.taxes:
        nums += [o for o in (tc.base, tc.rate, tc.amount) if o]
    nums += [o for o in (inv.subtotal, inv.discount, inv.retention, inv.total) if o]
    return all(o.value >= 0 for o in nums)


def mvp_predict(inv: Invoice, *, tol: int = 1) -> CalcState:
    return check_calc_consistency(
        inv, line_tolerance_cents=tol, tax_tolerance_cents=tol,
        total_tolerance_cents=max(2, tol * 2)).state


def _reduce(inv: Invoice, subset: str) -> Invoice:
    """Copia la factura dejando solo los invariantes del subconjunto pedido."""
    inv = copy.deepcopy(inv)
    if subset == "lines":
        inv.subtotal = None
        inv.discount = None
        inv.taxes = []
        inv.total = None
    elif subset == "lines+subtotal":
        inv.taxes = []
        inv.total = None
    # "full" -> sin cambios
    return inv


# ───────────────────────────── métricas ────────────────────────────────────

@dataclass
class Confusion:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else float("nan")

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else float("nan")

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        if p != p or r != r or (p + r) == 0:
            return float("nan")
        return 2 * p * r / (p + r)


def score(cases: List[Case], predict: Callable[[Invoice], CalcState]) -> Confusion:
    """Matriz de confusión SOLO sobre el universo detectable:
    positivos = faulty AND detectable; negativos = no-faulty. Los casos faulty-pero-
    no-detectables (satisface invariantes) se reportan aparte."""
    c = Confusion()
    for case in cases:
        if case.is_faulty and not case.detectable:
            continue  # límite conocido, va aparte
        pred_pos = predict(case.invoice) is CalcState.INCONSISTENT
        if case.is_faulty:
            c.tp += pred_pos
            c.fn += not pred_pos
        else:
            c.fp += pred_pos
            c.tn += not pred_pos
    return c


def _pct(x: float) -> str:
    return "  n/a" if x != x else f"{x*100:5.1f}%"


# ───────────────────────────── reporte ─────────────────────────────────────

def main(per_class: int = 20, seed: int = 7) -> None:
    cases = build_corpus(per_class=per_class, seed=seed)
    print(f"Corpus: {len(cases)} facturas ({per_class}/clase × {len(FAULT_CLASSES)} clases), seed={seed}\n")

    # ── A vs B ──
    # baseline campo-a-campo: solo marca si hay un número inválido (no hay chequeo
    # relacional), por eso no detecta inconsistencias aritméticas entre campos.
    base = Confusion()
    for case in cases:
        if case.is_faulty and not case.detectable:
            continue
        pred_pos = not baseline_field_check(case.invoice)  # solo marca si hay número inválido
        if case.is_faulty:
            base.tp += pred_pos; base.fn += not pred_pos
        else:
            base.fp += pred_pos; base.tn += not pred_pos
    mvp = score(cases, lambda inv: mvp_predict(inv, tol=1))

    print("== Condición A (campo-a-campo, baseline) vs B (invariantes, MVP) ==")
    print(f"{'cond':<26}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}{'prec':>8}{'rec':>8}{'F1':>8}")
    for name, c in [("A: campo-a-campo", base), ("B: invariantes (MVP)", mvp)]:
        print(f"{name:<26}{c.tp:>4}{c.fp:>4}{c.fn:>4}{c.tn:>4}"
              f"{_pct(c.precision):>8}{_pct(c.recall):>8}{_pct(c.f1):>8}")

    # ── desglose por clase (MVP, ε=1) ──
    print("\n== Detección por clase de fallo (MVP, ε=1) ==")
    print(f"{'clase':<22}{'n':>4}{'detectadas':>12}{'estado esperado':>20}")
    for fc in FAULT_CLASSES:
        sub = [c for c in cases if c.fault_class == fc]
        det = sum(mvp_predict(c.invoice, tol=1) is CalcState.INCONSISTENT for c in sub)
        if sub[0].is_faulty and sub[0].detectable:
            exp = "INCONSISTENT"
        elif sub[0].is_faulty and not sub[0].detectable:
            exp = "COHERENT (límite)"
        else:
            exp = "COHERENT"
        print(f"{fc:<22}{len(sub):>4}{det:>12}{exp:>20}")

    # ── ablación de tolerancia ε (foco: falsos positivos en redondeo legítimo) ──
    print("\n== Ablación: tolerancia ε (céntimos) ==")
    print(f"{'ε':>3}{'TP':>5}{'FP':>5}{'FN':>5}{'TN':>5}{'FP_redondeo':>13}{'rec':>8}")
    for tol in (0, 1, 2):
        c = score(cases, lambda inv, t=tol: mvp_predict(inv, tol=t))
        fp_round = sum(
            mvp_predict(cs.invoice, tol=tol) is CalcState.INCONSISTENT
            for cs in cases if cs.fault_class == "rounding_legit")
        print(f"{tol:>3}{c.tp:>5}{c.fp:>5}{c.fn:>5}{c.tn:>5}{fp_round:>13}{_pct(c.recall):>8}")

    # ── ablación de subconjunto de invariantes ──
    print("\n== Ablación: subconjunto de invariantes (ε=1) ==")
    print(f"{'subset':<16}{'TP':>5}{'FP':>5}{'FN':>5}{'TN':>5}{'rec':>8}  clases que se escapan")
    for subset in ("lines", "lines+subtotal", "full"):
        reduced = [Case(_reduce(c.invoice, subset), c.is_faulty, c.detectable,
                        c.fault_class, c.note) for c in cases]
        c = score(reduced, lambda inv: mvp_predict(inv, tol=1))
        # clases faulty-detectables con recall 0 bajo este subset
        escapan = []
        for fc in FAULT_CLASSES:
            sub = [cs for cs in reduced if cs.fault_class == fc and cs.is_faulty and cs.detectable]
            if sub and not any(mvp_predict(cs.invoice, tol=1) is CalcState.INCONSISTENT for cs in sub):
                escapan.append(fc)
        print(f"{subset:<16}{c.tp:>5}{c.fp:>5}{c.fn:>5}{c.tn:>5}{_pct(c.recall):>8}  "
              f"{', '.join(escapan) if escapan else '—'}")

    # ── límite conocido ──
    print("\n== Límite declarado (error que satisface invariantes) ==")
    sub = [c for c in cases if c.fault_class == "satisfies_invariants"]
    det = sum(mvp_predict(c.invoice, tol=1) is CalcState.INCONSISTENT for c in sub)
    print(f"faulty pero no detectable: {len(sub)} casos, detectados {det} "
          f"(esperado 0; SAT no prueba veracidad).")


if __name__ == "__main__":
    main()
