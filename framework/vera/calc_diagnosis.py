# Autor: @Raven
"""VERA — DIAGNÓSTICO de la dimensión de consistencia de cálculo (fase 2, sin Z3).

Cuando ``check_calc_consistency`` devuelve INCONSISTENT, esta capa responde las tres
preguntas del paper que la detección sola no contesta:

  1. REPARACIONES CANDIDATAS (MCS local): por cada invariante violado, qué valor de
     cada observación restauraría la consistencia (fijando las demás). Es el ejemplo
     del paper: cantidad=3, precio=40, importe=150 → 3 reparaciones, una por campo.
  2. CONFLICTO MÍNIMO (hitting set global): el conjunto MÁS CHICO de observaciones que,
     si estuvieran mal, explica TODOS los invariantes rotos. Un importe de línea mal
     rompe línea + subtotal + total; el culpable mínimo es esa UNA observación.
  3. ATRIBUCIÓN (honesta): clasifica el origen probable (OCR/indeterminado) a partir de
     patrones de lectura (dígito perdido, transposición, un dígito distinto). SIN
     evidencia de región OCR, el default es INDETERMINADO (no se culpa al LLM, como el
     paper). Los hooks para evidencia de región quedan para cuando se cablee.

Todo Python puro, exacto (Fraction), y determinista. NO usa Z3 (no está en el venv):
para facturas los conflictos son chicos y el hitting set se enumera por fuerza bruta.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from typing import Any, Dict, List, Optional, Tuple

from .calc_consistency import CalcResult, Violation
from .vera_tribunal import parse_numeric_mentions

# Clases de origen (Cuadro 2 del paper). Sin evidencia → INDETERMINADO.
ORIGIN_OCR = "ERROR_OCR"
ORIGIN_LLM = "ERROR_LLM"
ORIGIN_DOC = "ERROR_DOCUMENTO"
ORIGIN_INDET = "ERROR_INDETERMINADO"

_MONEY_ROLES = {"amount", "price", "base", "subtotal", "total", "disc", "sur", "retention",
                "debit", "credit", "assets", "liabilities", "equity"}


# ───────────────────────────── formato ─────────────────────────────────────

def _pesos(cents: Fraction) -> str:
    v = cents / 100
    return f"{float(v):,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _fmt(value: Fraction, role: str) -> str:
    if role == "rate":
        return f"{float(value) * 100:.4f}".rstrip("0").rstrip(".") + "%"
    if role == "qty":
        return str(value) if value.denominator != 1 else str(value.numerator)
    return _pesos(value)


# ───────────────────────────── atribución ──────────────────────────────────

def _to_int(value: Fraction, role: str) -> Optional[int]:
    """Entero representativo para comparar patrones de dígitos (céntimos para dinero)."""
    if role == "rate":
        return None
    v = value if role == "qty" else value  # dinero ya está en céntimos
    return int(v) if v.denominator == 1 else int(v + Fraction(1, 2))


def _ocr_pattern(observed: Optional[int], suggested: Optional[int]) -> Optional[str]:
    """Devuelve el patrón de lectura OCR si observado↔sugerido encaja en uno, o None."""
    if observed is None or suggested is None or observed == suggested:
        return None
    a, b = abs(observed), abs(suggested)
    for k in (10, 100, 1000):
        if a == b * k or b == a * k:
            return f"factor ×{k} (dígito perdido/agregado)"
    sa, sb = str(a), str(b)
    if len(sa) == len(sb):
        diffs = [i for i in range(len(sa)) if sa[i] != sb[i]]
        if len(diffs) == 1:
            return "un dígito distinto (confusión de lectura)"
        if (len(diffs) == 2 and diffs[1] == diffs[0] + 1
                and sa[diffs[0]] == sb[diffs[1]] and sa[diffs[1]] == sb[diffs[0]]):
            return "dígitos transpuestos"
    return None


def ocr_amounts(ocr_text: Optional[str]) -> Optional[List[int]]:
    """Montos (céntimos enteros) presentes en el texto OCR, para la atribución.
    ``None`` si no hay OCR → la atribución cae al heurístico de dígitos."""
    if not ocr_text:
        return None
    out: List[int] = []
    for m in parse_numeric_mentions(str(ocr_text)):
        if m.is_amount and m.magnitude is not None:
            out.append(int(Fraction(m.magnitude) * 100))
    return out


def _near(cents: Optional[int], amounts: List[int]) -> bool:
    return cents is not None and any(abs(cents - a) <= 1 for a in amounts)


def _attribute(observed: Fraction, suggested: Fraction, role: str,
               ocr_cents: Optional[List[int]] = None) -> Tuple[str, str]:
    """Atribuye el origen. Con evidencia OCR (``ocr_cents``) distingue OCR/LLM/DOCUMENTO
    (Cuadro 2 del paper); sin ella, heurístico de patrones de dígitos → OCR/INDETERMINADO."""
    if ocr_cents is not None and role in _MONEY_ROLES:
        obs_c, sug_c = _to_int(observed, role), _to_int(suggested, role)
        sug_in, obs_in = _near(sug_c, ocr_cents), _near(obs_c, ocr_cents)
        if sug_in and not obs_in:
            return ORIGIN_LLM, (f"el OCR muestra {_fmt(suggested, role)} pero el LLM "
                                f"extrajo {_fmt(observed, role)} (divergencia LLM↔OCR)")
        if obs_in:
            pat = _ocr_pattern(obs_c, sug_c)
            if pat:
                return ORIGIN_OCR, f"el valor extraído está en el OCR; patrón {pat}"
            return ORIGIN_DOC, ("OCR fiel al valor extraído y aun así no cierra "
                                "(inconsistencia del documento)")
    pat = _ocr_pattern(_to_int(observed, role), _to_int(suggested, role))
    if pat is not None:
        return ORIGIN_OCR, f"patrón de lectura: {pat}"
    return ORIGIN_INDET, "sin evidencia suficiente para atribuir (no se culpa al LLM)"


# ───────────────────────────── reparaciones por tipo ───────────────────────

def _val(term) -> Optional[Fraction]:
    return term[1] if term and term[1] is not None else None


def _repairs_for(v: Violation, ocr_cents: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Valor que restauraría la consistencia por cada observación del invariante."""
    t = v.terms
    out: List[Tuple[str, str, Fraction, Fraction]] = []  # (obs_id, role, observed, suggested)

    if v.kind == "line_product":
        q, p, a = _val(t["qty"]), _val(t["price"]), _val(t["amount"])
        d = _val(t["disc"]) if "disc" in t else Fraction(0)
        out.append((t["amount"][0], "amount", a, q * p - d))
        if p:
            out.append((t["qty"][0], "qty", q, (a + d) / p))
        if q:
            out.append((t["price"][0], "price", p, (a + d) / q))
        if "disc" in t:
            out.append((t["disc"][0], "disc", d, q * p - a))

    elif v.kind in ("tax_product", "deduction_product"):
        b, r, a = _val(t["base"]), _val(t["rate"]), _val(t["amount"])
        out.append((t["amount"][0], "amount", a, b * r))
        if r:
            out.append((t["base"][0], "base", b, a / r))
        if b:
            out.append((t["rate"][0], "rate", r, a / b))

    elif v.kind == "subtotal_sum":
        sub = _val(t["subtotal"])
        lines = t["lines"]
        S = sum((val for _id, val in lines), Fraction(0))
        disc = _val(t["disc"]) if "disc" in t else Fraction(0)
        sur = _val(t["sur"]) if "sur" in t else Fraction(0)
        out.append((t["subtotal"][0], "subtotal", sub, S - disc + sur))
        for lid, lval in lines:
            out.append((lid, "amount", lval, sub - (S - lval) + disc - sur))
        if "disc" in t:
            out.append((t["disc"][0], "disc", disc, S + sur - sub))
        if "sur" in t:
            out.append((t["sur"][0], "sur", sur, sub - S + disc))

    elif v.kind == "total_sum":
        total = _val(t["total"])
        base_for_total = _val(t["base_for_total"])
        ret = _val(t["retention"]) if "retention" in t else Fraction(0)
        out.append((t["total"][0], "total", total, base_for_total - ret))
        if "retention" in t:
            out.append((t["retention"][0], "retention", ret, base_for_total - total))

    elif v.kind == "debe_haber":
        sd, sc = _val(t["debit_total"]), _val(t["credit_total"])
        # el conflicto es Σdebe ≠ Σhaber; balancear igualando una suma a la otra
        out.append(("Σhaber", "credit", sc, sd))
        out.append(("Σdebe", "debit", sd, sc))

    elif v.kind == "balance_equation":
        a = _val(t["assets"])
        liab = _val(t["liabilities"]) if "liabilities" in t else Fraction(0)
        eq = _val(t["equity"]) if "equity" in t else Fraction(0)
        out.append((t["assets"][0], "assets", a, liab + eq))
        if "liabilities" in t:
            out.append((t["liabilities"][0], "liabilities", liab, a - eq))
        if "equity" in t:
            out.append((t["equity"][0], "equity", eq, a - liab))

    elif v.kind == "statement_balance":
        op, cl = _val(t["opening"]), _val(t["closing"])
        cred, deb = _val(t["credits"]), _val(t["debits"])
        out.append((t["closing"][0], "total", cl, op + cred - deb))
        out.append((t["opening"][0], "subtotal", op, cl - cred + deb))

    repairs = []
    for obs_id, role, observed, suggested in out:
        plausible = _plausible(role, suggested)
        origin, reason = _attribute(observed, suggested, role, ocr_cents)
        repairs.append({
            "obs_id": obs_id, "role": role,
            "observed": _fmt(observed, role), "suggested": _fmt(suggested, role),
            "plausible": plausible, "origin": origin, "reason": reason,
        })
    return repairs


def _plausible(role: str, suggested: Fraction) -> bool:
    """Filtro de dominio: descarta reparaciones imposibles (qty no entera, negativos, tasa fuera de rango)."""
    if role == "qty":
        return suggested > 0 and suggested.denominator == 1
    if role == "rate":
        return Fraction(0) < suggested <= 1
    if role in _MONEY_ROLES:
        return suggested >= 0
    return True


# ───────────────────────────── conflicto mínimo (hitting set) ──────────────

def _min_hitting_set(sets: List[List[str]], prefer: Optional[set] = None,
                     max_size: int = 3) -> List[str]:
    """Conjunto MÁS CHICO de obs que intersecta todos los ``sets`` (fuerza bruta acotada).
    Es el culpable mínimo: las menos observaciones que explican todos los conflictos.
    Entre los de tamaño mínimo, prefiere los que caen en ``prefer`` (obs con reparación
    plausible) → así no se señala una observación que no se puede corregir."""
    prefer = prefer or set()
    universe: List[str] = []
    for s in sets:
        for x in s:
            if x not in universe:
                universe.append(x)
    for size in range(1, min(max_size, len(universe)) + 1):
        candidates = [set(c) for c in combinations(universe, size)
                      if all(set(c) & set(s) for s in sets)]
        if candidates:
            # máxima cobertura de 'prefer' (todas plausibles mejor que ninguna)
            best = max(candidates, key=lambda cs: len(cs & prefer))
            return [x for x in universe if x in best]
    return universe


# ───────────────────────────── API ─────────────────────────────────────────

def diagnose(result: CalcResult, ocr_text: Optional[str] = None) -> Dict[str, Any]:
    """Diagnóstico de un ``CalcResult`` INCONSISTENT: reparaciones + conflicto mínimo +
    atribución. Con ``ocr_text`` (evidencia de VERA) distingue OCR/LLM/DOCUMENTO; sin él,
    heurístico de dígitos. Estados no-inconsistentes → ``{"applicable": False}``."""
    if not result.demonstrated_inconsistent:
        return {"applicable": False, "state": result.state.value}

    ocr_cents = ocr_amounts(ocr_text)
    conflicts = []
    obs_sets: List[List[str]] = []
    plausible_obs: set = set()
    for v in result.violations:
        repairs = _repairs_for(v, ocr_cents)
        conflicts.append({"rule": v.rule, "kind": v.kind,
                          "obs_ids": list(v.obs_ids), "repairs": repairs})
        obs_sets.append(list(v.obs_ids))
        plausible_obs |= {r["obs_id"] for r in repairs if r["plausible"]}

    culprits = _min_hitting_set(obs_sets, prefer=plausible_obs)
    # Mejor reparación PLAUSIBLE por observación (línea > impuesto > agregados).
    _prio = {"line_product": 0, "tax_product": 1, "deduction_product": 1,
             "subtotal_sum": 2, "total_sum": 3, "balance_equation": 2, "debe_haber": 2,
             "statement_balance": 2}
    best: Dict[str, Dict[str, Any]] = {}
    for c in sorted(conflicts, key=lambda c: _prio.get(c["kind"], 9)):
        for rep in c["repairs"]:
            if rep["obs_id"] in culprits and rep["plausible"] and rep["obs_id"] not in best:
                best[rep["obs_id"]] = {**rep, "rule": c["rule"]}

    # ¿Culpable ÚNICO fuerte? Exactamente UNA observación plausible que, sola, resuelve
    # TODOS los conflictos. Si dos obs distintas podrían explicarlo (p.ej. un conflicto
    # aislado con precio=50 o importe=120), NO hay culpable único: son alternativas (los
    # MCS del paper) y la evidencia (región OCR) decide.
    resolving = [o for o in plausible_obs if all(o in s for s in obs_sets)]
    single = len(resolving) == 1 and resolving[0] in best
    if single:
        culprits = resolving
        candidates = [best[resolving[0]]]
    else:
        candidates = [r for c in conflicts for r in c["repairs"] if r["plausible"]]

    return {
        "applicable": True,
        "conflicts": conflicts,
        "minimal_culprits": culprits,
        "single_culprit": single,
        "candidate_repairs": candidates,
        "summary": _summary(single, culprits, best, candidates),
    }


def _summary(single: bool, culprits: List[str], best: Dict[str, Dict[str, Any]],
             candidates: List[Dict[str, Any]]) -> str:
    if single:
        b = best[culprits[0]]
        return (f"Culpable mínimo: {b['obs_id']} (debería ser {b['suggested']}, "
                f"observado {b['observed']}; {b['origin']}: {b['reason']}).")
    if candidates:
        partes = [f"{r['obs_id']}→{r['suggested']}" for r in candidates]
        return (f"Conflicto no resuelto: {len(candidates)} reparación(es) plausible(s): "
                + ", ".join(partes) + ". La evidencia (región OCR) decide cuál.")
    return f"Conflicto mínimo: {culprits} (sin reparación de dominio plausible)."


__all__ = ["diagnose", "ocr_amounts",
           "ORIGIN_OCR", "ORIGIN_LLM", "ORIGIN_DOC", "ORIGIN_INDET"]
