# Autor: @Raven
"""VERA — consistencia de cálculo CONTABLE: partida doble y ecuación patrimonial.

Extiende la dimensión de cálculo a documentos contables con dos invariantes clásicos,
verificados de forma determinista y con aritmética exacta (céntimos enteros), reusando
los tipos compartidos (``CalcResult``/``CalcState``/``Violation``) para que el diagnóstico,
el veredicto unificado y el indicador del front funcionen igual que con facturas:

  - **Partida doble** (asiento):  Σ debe = Σ haber.
  - **Ecuación patrimonial** (balance):  activo = pasivo + patrimonio neto.

Honestidad (grounding): un invariante solo se evalúa si los datos lo respaldan; si faltan
datos → ``INCOMPLETE``, nunca una inconsistencia inventada. La asimetría UNSAT=certeza /
SAT≠verdad es la misma que en facturas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional

from .calc_consistency import (
    CalcResult, CalcState, Obs, Role, Violation, _fmt_cents,
)


@dataclass
class AccountEntry:
    """Una línea de asiento: un monto al DEBE o al HABER (una de las dos, o ambas)."""
    account: str
    debit: Optional[Obs] = None
    credit: Optional[Obs] = None


@dataclass
class Journal:
    """Asiento contable: líneas al debe/haber. Invariante: Σ debe = Σ haber."""
    entries: List[AccountEntry] = field(default_factory=list)


@dataclass
class Balance:
    """Situación patrimonial. Invariante: activo = pasivo + patrimonio neto."""
    assets: Optional[Obs] = None       # activo
    liabilities: Optional[Obs] = None  # pasivo
    equity: Optional[Obs] = None       # patrimonio neto


@dataclass
class Statement:
    """Resumen de cuenta / mayor / extracto. Invariante:
    saldo_inicial + Σcréditos − Σdébitos = saldo_final. (Créditos suman, débitos restan,
    desde la óptica del titular de la cuenta.)"""
    opening: Optional[Obs] = None                 # saldo inicial
    credits: List[Obs] = field(default_factory=list)  # créditos (suman)
    debits: List[Obs] = field(default_factory=list)   # débitos (restan)
    closing: Optional[Obs] = None                 # saldo final


@dataclass
class AccountingDoc:
    """Documento contable: asiento, balance y/o resumen de cuenta (lo que haya)."""
    journal: Optional[Journal] = None
    balance: Optional[Balance] = None
    statement: Optional[Statement] = None


def check_accounting(doc: AccountingDoc, *, tolerance_cents: int = 2) -> CalcResult:
    """Verifica los invariantes contables que los datos respaldan."""
    try:
        violations: List[Violation] = []
        incomplete: List[str] = []
        checked = 0

        # (1) Partida doble: Σ debe = Σ haber.
        if doc.journal is not None and doc.journal.entries:
            debits = [e.debit for e in doc.journal.entries if e.debit is not None]
            credits = [e.credit for e in doc.journal.entries if e.credit is not None]
            if debits and credits:
                checked += 1
                sd = sum((o.value for o in debits), Fraction(0))
                sc = sum((o.value for o in credits), Fraction(0))
                residual = abs(sd - sc)
                tol = tolerance_cents + max(0, len(doc.journal.entries) - 1) * tolerance_cents
                if residual > tol:
                    violations.append(Violation(
                        rule="asiento: Σdebe = Σhaber",
                        obs_ids=[o.obs_id for o in debits] + [o.obs_id for o in credits],
                        expected=f"{_fmt_cents(sd)}c (debe)",
                        found=f"{_fmt_cents(sc)}c (haber)",
                        residual_cents=_fmt_cents(residual),
                        tolerance_cents=tol,
                        kind="debe_haber",
                        terms={"debit_total": (None, sd), "credit_total": (None, sc)},
                    ))
            else:
                incomplete.append("asiento: falta el debe o el haber (no se puede balancear)")

        # (2) Ecuación patrimonial: activo = pasivo + patrimonio neto.
        b = doc.balance
        if b is not None and b.assets is not None and (b.liabilities is not None
                                                       or b.equity is not None):
            checked += 1
            liab = b.liabilities.value if b.liabilities is not None else Fraction(0)
            eq = b.equity.value if b.equity is not None else Fraction(0)
            expected = liab + eq
            residual = abs(b.assets.value - expected)
            if residual > tolerance_cents:
                ids = [o.obs_id for o in (b.assets, b.liabilities, b.equity) if o is not None]
                terms = {"assets": (b.assets.obs_id, b.assets.value)}
                if b.liabilities is not None:
                    terms["liabilities"] = (b.liabilities.obs_id, b.liabilities.value)
                if b.equity is not None:
                    terms["equity"] = (b.equity.obs_id, b.equity.value)
                violations.append(Violation(
                    rule="balance: activo = pasivo + patrimonio neto",
                    obs_ids=ids,
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(b.assets.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=tolerance_cents,
                    kind="balance_equation", terms=terms,
                ))

        # (3) Resumen de cuenta: saldo_inicial + Σcréditos − Σdébitos = saldo_final.
        s = doc.statement
        if (s is not None and s.opening is not None and s.closing is not None
                and not s.credits and not s.debits
                and abs(s.opening.value - s.closing.value) > tolerance_cents):
            # Grounding: sin movimientos extraídos no se puede explicar la variación del
            # saldo → INCOMPLETE, no se inventa una inconsistencia.
            incomplete.append("resumen: sin movimientos extraídos; no se verifica la variación del saldo")
        elif s is not None and s.opening is not None and s.closing is not None:
            checked += 1
            scred = sum((o.value for o in s.credits), Fraction(0))
            sdeb = sum((o.value for o in s.debits), Fraction(0))
            expected = s.opening.value + scred - sdeb
            tol = tolerance_cents + max(0, len(s.credits) + len(s.debits) - 1) * tolerance_cents
            residual = abs(expected - s.closing.value)
            if residual > tol:
                ids = ([s.opening.obs_id, s.closing.obs_id]
                       + [o.obs_id for o in s.credits] + [o.obs_id for o in s.debits])
                violations.append(Violation(
                    rule="resumen: saldo_inicial + Σcréditos - Σdébitos = saldo_final",
                    obs_ids=ids,
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(s.closing.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=tol,
                    kind="statement_balance",
                    terms={"opening": (s.opening.obs_id, s.opening.value),
                           "closing": (s.closing.obs_id, s.closing.value),
                           "credits": (None, scred), "debits": (None, sdeb)},
                ))
        elif s is not None and (s.opening is not None or s.closing is not None):
            incomplete.append("resumen: falta saldo inicial o saldo final")

        if violations:
            return CalcResult(CalcState.INCONSISTENT, checked, violations, incomplete)
        if checked == 0:
            return CalcResult(CalcState.INCOMPLETE, 0, [],
                              incomplete or ["sin invariantes contables aplicables (faltan datos)"])
        return CalcResult(CalcState.COHERENT, checked, [], incomplete)
    except Exception as exc:  # noqa: BLE001 — honest-error del verificador
        return CalcResult(CalcState.ERROR, 0, [], [], error=f"{type(exc).__name__}: {exc}")


__all__ = [
    "AccountEntry", "Journal", "Balance", "Statement", "AccountingDoc",
    "check_accounting",
]
