# Autor: @Raven
"""VERA — dimensión de CONSISTENCIA DE CÁLCULO (MVP, condición B del paper).

Propuesta: "VERA: dimensión de consistencia de cálculo con diagnóstico de
conflictos mínimos" (A. Jaime, 2026-10-21). Este módulo implementa la **condición
B** (verificación determinista de invariantes, SIN solver): detecta que las
entidades numéricas extraídas de una factura violan los invariantes aritméticos
que los propios datos respaldan, con aritmética EXACTA (céntimos enteros +
racionales, nunca coma flotante) y tolerancia de redondeo EXPLÍCITA.

ALCANCE / HONESTIDAD (lo que ESTE MVP hace y lo que NO)
------------------------------------------------------
Hace (condición B):
  - DETECCIÓN: ¿el conjunto de observaciones + reglas es insatisfacible? (binario).
  - LOCALIZACIÓN ligera: qué invariante(s) falla(n) y qué observaciones participan,
    con el residuo exacto. NO es un MUS mínimo formal.

NO hace (fases posteriores del paper, requieren decisión/medición):
  - MUS/MCS formales ni enumeración de reparaciones (necesita Z3; NO está en el venv).
  - Atribución de origen (OCR/LLM/documento/regla): requiere evidencia de región.
    Aquí NO se culpa a nadie; solo se reporta la inconsistencia y su residuo.

Garantías (Cuadro 1 del paper): afirma CORRECCIÓN ARITMÉTICA y CONSISTENCIA DE
ENTIDADES. La asimetría clave: UNSAT = **certeza de error**; SAT ("coherente") NO
prueba veracidad. Un error que SATISFACE los invariantes no se detecta (límite
declarado). La regla que no se negocia: una inconsistencia demostrada nunca
produce una salida aceptada.

Una relación solo se convierte en invariante si los datos la respaldan; si falta
un dato, el resultado es ``INCOMPLETE`` (verificación incompleta), no una
restricción inventada.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from fractions import Fraction
from typing import Any, Dict, List, Optional, Union

# Dinero: SIEMPRE en céntimos enteros (Fraction para no perder exactitud al
# multiplicar por cantidades/tasas racionales). Cantidades y tasas: Fraction.
Number = Union[int, float, str, Fraction]


class CalcState(StrEnum):
    """Estados mínimos del paper (§8), proyectables a la política de VERA."""
    COHERENT = "coherent"        # satisfacible bajo los invariantes aplicados
    INCONSISTENT = "inconsistent"  # inconsistencia DEMOSTRADA (bloquea aceptación)
    INCOMPLETE = "incomplete"    # faltan datos para formar invariantes
    ERROR = "error"              # fallo de formalización/ejecución del verificador


class Role(StrEnum):
    """Rol aritmético de una entidad numérica (lo que el role-tagging debe fijar)."""
    QTY = "qty"
    UNIT_PRICE = "unit_price"
    LINE_AMOUNT = "line_amount"
    LINE_DISCOUNT = "line_discount"
    SUBTOTAL = "subtotal"
    DISCOUNT = "discount"          # descuento de bloque
    SURCHARGE = "surcharge"        # recargo de bloque
    TAX_BASE = "tax_base"
    TAX_RATE = "tax_rate"
    TAX_AMOUNT = "tax_amount"
    RETENTION = "retention"
    TOTAL = "total"
    # Contables
    DEBIT = "debit"            # debe (asiento)
    CREDIT = "credit"          # haber (asiento)
    ASSETS = "assets"          # activo
    LIABILITIES = "liabilities"  # pasivo
    EQUITY = "equity"          # patrimonio neto


def to_cents(value: Number) -> Fraction:
    """Normaliza un importe a **céntimos exactos** (Fraction). Acepta int/float/str.

    Un ``float`` se pasa por ``str`` para no arrastrar el ruido binario (0.1+0.2).
    """
    if isinstance(value, Fraction):
        return value * 100
    if isinstance(value, int):
        return Fraction(value) * 100
    if isinstance(value, float):
        value = repr(value)
    # str: admite "1.234,56" (es-AR) o "1234.56"; aquí asumimos ya-normalizado numérico.
    return Fraction(str(value)) * 100


def to_fraction(value: Number) -> Fraction:
    """Normaliza una cantidad/tasa a Fraction exacta (sin escala de céntimos)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, float):
        return Fraction(str(value))
    return Fraction(str(value))


@dataclass
class Obs:
    """Una observación etiquetada (x = v), activable/retirable. ``provenance`` es
    libre (doc/página/bloque/región OCR/confianza) para la fase de atribución."""
    obs_id: str
    role: Role
    value: Fraction            # céntimos (dinero) o Fraction crudo (qty/rate)
    text: Optional[str] = None
    provenance: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LineItem:
    line_id: str
    qty: Optional[Obs] = None
    unit_price: Optional[Obs] = None
    amount: Optional[Obs] = None
    discount: Optional[Obs] = None


@dataclass
class TaxComponent:
    tax_id: str
    base: Optional[Obs] = None
    rate: Optional[Obs] = None     # p.ej. Fraction(21,100)
    amount: Optional[Obs] = None


@dataclass
class Deduction:
    """Un concepto que RESTA del bruto (nómina: jubilación, obra social, retención…).
    Mismo invariante ``base*rate=amount`` que un impuesto, pero su monto se RESTA del
    total (neto = bruto - Σdeducciones). ``rate`` opcional: si no hay tasa, solo cuenta
    su monto para la suma (deducción de monto fijo)."""
    ded_id: str
    base: Optional[Obs] = None
    rate: Optional[Obs] = None
    amount: Optional[Obs] = None


@dataclass
class Invoice:
    """Observaciones role-tagged de UNA factura/recibo (ya mapeadas desde la extracción)."""
    lines: List[LineItem] = field(default_factory=list)
    subtotal: Optional[Obs] = None
    discount: Optional[Obs] = None
    surcharge: Optional[Obs] = None
    taxes: List[TaxComponent] = field(default_factory=list)
    deductions: List[Deduction] = field(default_factory=list)
    retention: Optional[Obs] = None
    total: Optional[Obs] = None


@dataclass
class Violation:
    """Un invariante violado: conflicto ligero (NO un MUS formal).

    ``kind`` identifica la relación (``line_product``/``subtotal_sum``/``tax_product``/
    ``total_sum``) y ``terms`` lleva las observaciones participantes como
    ``{etiqueta: (obs_id, valor_Fraction)}`` para que la capa de diagnóstico calcule
    reparaciones candidatas. ``terms`` NO se serializa (lleva Fraction/estructura)."""
    rule: str                      # id legible de la regla aritmética
    obs_ids: List[str]             # observaciones que participan (candidatas a culpa)
    expected: str                  # lado esperado (en unidades legibles)
    found: str                     # lado encontrado
    residual_cents: str            # |esperado - encontrado| en céntimos exactos
    tolerance_cents: int
    kind: str = ""                 # tipo de relación (para el diagnóstico)
    terms: Dict[str, Any] = field(default_factory=dict)  # etiqueta -> (obs_id, valor)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule, "obs_ids": list(self.obs_ids),
            "expected": self.expected, "found": self.found,
            "residual_cents": self.residual_cents,
            "tolerance_cents": self.tolerance_cents,
            "kind": self.kind,
        }


@dataclass
class CalcResult:
    state: CalcState
    invariants_checked: int
    violations: List[Violation] = field(default_factory=list)
    incomplete_reasons: List[str] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        """Coherente SOLO si se comprobó algún invariante y no hubo violaciones."""
        return self.state is CalcState.COHERENT

    @property
    def demonstrated_inconsistent(self) -> bool:
        return self.state is CalcState.INCONSISTENT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "state": self.state.value,
            "invariants_checked": self.invariants_checked,
            "violations": [v.to_dict() for v in self.violations],
            "incomplete_reasons": list(self.incomplete_reasons),
            "error": self.error,
        }


def _fmt_cents(c: Fraction) -> str:
    """Céntimos exactos -> texto (sin perder exactitud; muestra fracción si la hay)."""
    if c.denominator == 1:
        return str(c.numerator)
    return f"{float(c):.4f}~"  # aproximado solo para lectura


# ───────────────────────────── núcleo (condición B) ────────────────────────

def check_calc_consistency(
    inv: Invoice,
    *,
    line_tolerance_cents: int = 1,
    tax_tolerance_cents: int = 2,
    total_tolerance_cents: int = 2,
) -> CalcResult:
    """Verifica los invariantes aritméticos que los datos respaldan (sin solver).

    Tolerancias EXPLÍCITAS por regla (redondeo legítimo); nunca se ajustan para que
    "algo pase". Los agregados (subtotal/total) escalan la tolerancia por el nº de
    sumandos, porque cada línea redondeada aporta su propio ε.
    """
    try:
        violations: List[Violation] = []
        incomplete: List[str] = []
        checked = 0

        # (1) Línea: qty * unit_price = amount (+ descuento de línea si existe).
        line_amounts: List[Fraction] = []
        n_line_terms = 0
        missing_line_amount = False  # alguna línea sin importe legible → subtotal INCOMPLETO
        for li in inv.lines:
            if li.amount is not None:
                line_amounts.append(li.amount.value)
                n_line_terms += 1
            if li.qty is None or li.unit_price is None or li.amount is None:
                if li.amount is None:
                    missing_line_amount = True
                    incomplete.append(f"línea {li.line_id}: sin importe")
                continue
            checked += 1
            expected = li.qty.value * li.unit_price.value  # qty(Fraction) * precio(céntimos)
            if li.discount is not None:
                expected = expected - li.discount.value
            residual = abs(expected - li.amount.value)
            if residual > line_tolerance_cents:
                ids = [o.obs_id for o in (li.qty, li.unit_price, li.amount, li.discount) if o]
                terms = {"qty": (li.qty.obs_id, li.qty.value),
                         "price": (li.unit_price.obs_id, li.unit_price.value),
                         "amount": (li.amount.obs_id, li.amount.value)}
                if li.discount is not None:
                    terms["disc"] = (li.discount.obs_id, li.discount.value)
                violations.append(Violation(
                    rule=f"line[{li.line_id}]: qty*price(-desc)=amount",
                    obs_ids=ids,
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(li.amount.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=line_tolerance_cents,
                    kind="line_product", terms=terms,
                ))

        # (2) Subtotal de bloque: Σ amount - descuento + recargo = subtotal.
        # Solo si TODAS las líneas tienen importe legible: con una celda ilegible no
        # se puede formar Σamount de forma confiable → verificación INCOMPLETA, no una
        # inconsistencia inventada (una fila OMITIDA, en cambio, sí dispara: todas las
        # líneas presentes tienen importe y su suma no cierra).
        if missing_line_amount and inv.subtotal is not None:
            incomplete.append("subtotal: hay líneas sin importe legible (Σamount incompleta)")
        elif inv.subtotal is not None and line_amounts:
            checked += 1
            expected = sum(line_amounts, Fraction(0))
            if inv.discount is not None:
                expected -= inv.discount.value
            if inv.surcharge is not None:
                expected += inv.surcharge.value
            tol = total_tolerance_cents + max(0, n_line_terms - 1) * line_tolerance_cents
            residual = abs(expected - inv.subtotal.value)
            if residual > tol:
                ids = ([li.amount.obs_id for li in inv.lines if li.amount]
                       + [o.obs_id for o in (inv.discount, inv.surcharge, inv.subtotal) if o])
                terms = {"subtotal": (inv.subtotal.obs_id, inv.subtotal.value),
                         "lines": [(li.amount.obs_id, li.amount.value)
                                   for li in inv.lines if li.amount]}
                if inv.discount is not None:
                    terms["disc"] = (inv.discount.obs_id, inv.discount.value)
                if inv.surcharge is not None:
                    terms["sur"] = (inv.surcharge.obs_id, inv.surcharge.value)
                violations.append(Violation(
                    rule="subtotal: Σamount - desc + recargo = subtotal",
                    obs_ids=ids,
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(inv.subtotal.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=tol,
                    kind="subtotal_sum", terms=terms,
                ))

        # (3) Impuesto por tipo: base * tasa = cuota.
        tax_sum: Fraction = Fraction(0)
        base_plus_tax: Fraction = Fraction(0)
        have_tax_terms = False
        for tc in inv.taxes:
            if tc.amount is not None:
                tax_sum += tc.amount.value
                have_tax_terms = True
            if tc.base is not None and tc.amount is not None:
                base_plus_tax += tc.base.value + tc.amount.value
            if tc.base is None or tc.rate is None or tc.amount is None:
                continue
            checked += 1
            expected = tc.base.value * tc.rate.value  # céntimos * Fraction
            residual = abs(expected - tc.amount.value)
            if residual > tax_tolerance_cents:
                violations.append(Violation(
                    rule=f"tax[{tc.tax_id}]: base*rate=amount",
                    obs_ids=[o.obs_id for o in (tc.base, tc.rate, tc.amount) if o],
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(tc.amount.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=tax_tolerance_cents,
                    kind="tax_product",
                    terms={"base": (tc.base.obs_id, tc.base.value),
                           "rate": (tc.rate.obs_id, tc.rate.value),
                           "amount": (tc.amount.obs_id, tc.amount.value)},
                ))

        # (3-bis) Deducciones (nómina): base*rate=amount por concepto; su monto RESTA.
        ded_sum: Fraction = Fraction(0)
        for dd in inv.deductions:
            if dd.amount is not None:
                ded_sum += dd.amount.value
            if dd.base is None or dd.rate is None or dd.amount is None:
                continue
            checked += 1
            expected = dd.base.value * dd.rate.value
            residual = abs(expected - dd.amount.value)
            if residual > tax_tolerance_cents:
                violations.append(Violation(
                    rule=f"deduction[{dd.ded_id}]: base*rate=amount",
                    obs_ids=[o.obs_id for o in (dd.base, dd.rate, dd.amount) if o],
                    expected=f"{_fmt_cents(expected)}c",
                    found=f"{_fmt_cents(dd.amount.value)}c",
                    residual_cents=_fmt_cents(residual),
                    tolerance_cents=tax_tolerance_cents,
                    kind="deduction_product",
                    terms={"base": (dd.base.obs_id, dd.base.value),
                           "rate": (dd.rate.obs_id, dd.rate.value),
                           "amount": (dd.amount.obs_id, dd.amount.value)},
                ))

        # (4) Total: base - retención - Σdeducciones = total. ``base`` = Σ(base+cuota) /
        #     subtotal+Σcuota / Σlíneas+Σcuota según los datos (factura). En nómina: base =
        #     bruto (subtotal), sin impuestos, y las deducciones lo bajan a neto.
        unexplained_gap = False  # gap total que podría ser un impuesto NO extraído
        if inv.total is not None:
            base_for_total: Optional[Fraction] = None
            source = None
            if have_tax_terms and base_plus_tax != 0:
                base_for_total = base_plus_tax
                source = "Σ(base+cuota)"
            elif inv.subtotal is not None:
                base_for_total = inv.subtotal.value + tax_sum
                source = "subtotal+Σcuota"
            elif line_amounts and not missing_line_amount:
                # Factura B / ticket: IVA incluido, sin subtotal ni cuota discriminada →
                # el invariante útil es total = Σlíneas (- retención).
                base_for_total = sum(line_amounts, Fraction(0)) + tax_sum
                source = "Σlíneas+Σcuota"
            if base_for_total is None and ded_sum != 0 and inv.subtotal is not None:
                # nómina pura: bruto declarado - deducciones = neto (sin impuestos ni líneas)
                base_for_total = inv.subtotal.value
                source = "bruto"
            if base_for_total is None:
                incomplete.append("total: sin base (ni bloques con base+cuota, ni subtotal, ni líneas completas)")
            else:
                checked += 1
                expected = base_for_total - ded_sum  # las deducciones RESTAN
                if inv.retention is not None:
                    expected -= inv.retention.value
                tol = total_tolerance_cents + max(0, len(inv.taxes) - 1) * tax_tolerance_cents
                if source == "Σlíneas+Σcuota":
                    # cada línea redondeada aporta su propio ε al agregado.
                    tol += max(0, n_line_terms - 1) * line_tolerance_cents
                residual = abs(expected - inv.total.value)
                # NO INVENTAR IMPUESTOS: si NO se extrajo ningún impuesto y el total es MAYOR
                # que lo que suman los componentes, ese gap podría ser un IVA/cargo no extraído.
                # No se puede afirmar inconsistencia (sería inventar que "no hay IVA") →
                # verificación INCOMPLETA. (Los impuestos solo SUMAN: un total MENOR que los
                # componentes sí es inconsistente, porque un impuesto faltante no lo explica.)
                if (residual > tol and not inv.taxes and not inv.deductions
                        and inv.total.value > expected):
                    checked -= 1  # no se pudo verificar el total
                    unexplained_gap = True
                    incomplete.append(
                        f"total: diferencia de {_fmt_cents(inv.total.value - expected)}c por "
                        "encima de los componentes; posible impuesto/cargo no extraído "
                        "(no se afirma inconsistencia)")
                elif residual > tol:
                    # El conjunto de obs del total incluye lo que compone ``base_for_total``
                    # según la fuente, para que el conflicto mínimo pueda identificar un
                    # culpable COMPARTIDO con línea/subtotal/impuesto (p.ej. un importe de
                    # línea mal rompe la línea Y el total vía Σlíneas → una sola obs).
                    ids = [o.obs_id for o in (inv.retention, inv.total) if o]
                    if source == "Σlíneas+Σcuota":
                        ids += [li.amount.obs_id for li in inv.lines if li.amount]
                    elif source in ("subtotal+Σcuota", "bruto") and inv.subtotal is not None:
                        ids.append(inv.subtotal.obs_id)
                    ids += [o.obs_id for tc in inv.taxes for o in (tc.base, tc.amount) if o]
                    ids += [dd.amount.obs_id for dd in inv.deductions if dd.amount]
                    terms = {"total": (inv.total.obs_id, inv.total.value),
                             # base neta: ya con las deducciones restadas, para la reparación
                             "base_for_total": (None, base_for_total - ded_sum), "source": source}
                    if inv.retention is not None:
                        terms["retention"] = (inv.retention.obs_id, inv.retention.value)
                    violations.append(Violation(
                        rule=f"total: {source} - retención = total",
                        obs_ids=ids,
                        expected=f"{_fmt_cents(expected)}c",
                        found=f"{_fmt_cents(inv.total.value)}c",
                        residual_cents=_fmt_cents(residual),
                        tolerance_cents=tol,
                        kind="total_sum", terms=terms,
                    ))

        if violations:
            return CalcResult(CalcState.INCONSISTENT, checked, violations, incomplete)
        if checked == 0:
            return CalcResult(CalcState.INCOMPLETE, 0, [],
                              incomplete or ["sin invariantes aplicables (faltan datos)"])
        # SEGURIDAD: si una línea existe pero su importe no se pudo leer (OCR corrupto),
        # NO se puede certificar la consistencia de líneas aunque el resto cierre → el
        # veredicto es INCOMPLETE, nunca COHERENT. Evita aceptar en silencio una factura
        # con un importe ilegible (una fila OMITIDA por completo, en cambio, sí dispara el
        # invariante de subtotal y cae en INCONSISTENT más arriba).
        # Tampoco se afirma COHERENT si quedó un gap de total que podría ser un impuesto no
        # extraído (no inventar que "no hay IVA") o si falta leer el importe de una línea.
        if missing_line_amount or unexplained_gap:
            return CalcResult(CalcState.INCOMPLETE, max(0, checked), [], incomplete)
        return CalcResult(CalcState.COHERENT, checked, [], incomplete)
    except Exception as exc:  # noqa: BLE001 — honest-error del verificador
        return CalcResult(CalcState.ERROR, 0, [], [], error=f"{type(exc).__name__}: {exc}")


# ───────────────────────────── integración VERA ────────────────────────────

def attach_calc(result: Any, calc: CalcResult) -> tuple[Any, bool]:
    """Adjunta el veredicto de cálculo de forma NO DESTRUCTIVA (clave ``_vera_calc``),
    como ``integration.attach_report``. Devuelve ``(result, blocked)`` donde
    ``blocked`` es True SI hay inconsistencia demostrada (regla de oro: no puede
    atravesar la frontera de validación como salida aceptada)."""
    blocked = calc.demonstrated_inconsistent
    if isinstance(result, dict):
        result = {**result, "_vera_calc": calc.to_dict()}
    return result, blocked


__all__ = [
    "CalcState", "Role", "Obs", "LineItem", "TaxComponent", "Deduction", "Invoice",
    "Violation", "CalcResult", "check_calc_consistency", "attach_calc",
    "to_cents", "to_fraction",
]
