# Autor: @Raven
"""VERA — role-tagging para la dimensión de consistencia de cálculo.

Puente entre una EXTRACCIÓN del LLM (dict header + array de líneas, valores string
en formato libre es-AR/intl) y las observaciones role-tagged (``Invoice``) que
consume ``calc_consistency.check_calc_consistency``.

Decoplado a propósito: NO modifica ``EntitySpec`` (congelado y *fingerprinted* —
tocarlo invalidaría los fingerprints de auditoría de VERA). El mapeo de ROLES
aritméticos vive en su propio artefacto, el bloque ``calc`` del contrato:

    <doc_type>:
      entities: [...]            # el contrato de cobertura de VERA (sin cambios)
      calc:                      # NUEVO, opcional — solo esta dimensión lo lee
        lines:  {array: "items", qty: "cantidad", price: "precio_unit", amount: "importe"}
        subtotal: "subtotal"
        total:    "total"
        taxes:
          - {base: "base_imponible", rate: "alicuota_iva", amount: "iva", id: "iva"}

Si el doc_type no trae bloque ``calc`` → ``load_calc_contract`` devuelve ``None`` y
la dimensión queda inerte (como el resto de VERA: opt-in, cero efecto por defecto).

Parseo numérico: reusa ``parse_numeric_mentions`` de ``vera_tribunal`` (misma
canonicalización que el anclaje: 1.234,56 ≡ 1234.56), cuyo ``magnitude`` es
``Decimal`` EXACTO → se convierte a ``Fraction`` sin pérdida (nada de float).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from typing import Any, Optional

from .calc_consistency import (
    CalcResult, Deduction, Invoice, LineItem, Obs, Role, TaxComponent,
    check_calc_consistency, to_cents,
)
from .vera_tribunal import parse_numeric_mentions


# ───────────────────────────── mapeo de roles ──────────────────────────────

@dataclass(frozen=True)
class CalcLineMap:
    """Dónde están las líneas en la extracción y qué clave es qué rol."""
    array: str                      # clave del array de líneas (p.ej. "items")
    qty: str
    price: str
    amount: str
    discount: Optional[str] = None


@dataclass(frozen=True)
class CalcTaxMap:
    base: Optional[str] = None
    rate: Optional[str] = None
    amount: Optional[str] = None
    tax_id: str = "iva"


@dataclass(frozen=True)
class CalcDeductionMap:
    """Deducción de nómina que RESTA (jubilación/obra social/…): base*rate=amount."""
    base: Optional[str] = None
    rate: Optional[str] = None
    amount: Optional[str] = None
    ded_id: str = "ded"


@dataclass(frozen=True)
class CalcContract:
    """Mapeo key-de-extracción → rol aritmético (artefacto de la dimensión calc)."""
    lines: Optional[CalcLineMap] = None
    subtotal: Optional[str] = None
    discount: Optional[str] = None
    surcharge: Optional[str] = None
    retention: Optional[str] = None
    total: Optional[str] = None
    taxes: tuple[CalcTaxMap, ...] = ()
    deductions: tuple[CalcDeductionMap, ...] = ()

    @property
    def is_empty(self) -> bool:
        return (self.lines is None and not self.taxes and not self.deductions
                and self.total is None and self.subtotal is None)


def calc_contract_from_dict(d: dict[str, Any]) -> CalcContract:
    """Construye un ``CalcContract`` desde el bloque ``calc`` (dict) del contrato."""
    lm = None
    if isinstance(d.get("lines"), dict):
        l = d["lines"]
        lm = CalcLineMap(
            array=l["array"], qty=l["qty"], price=l["price"], amount=l["amount"],
            discount=l.get("discount"))
    taxes = tuple(
        CalcTaxMap(base=t.get("base"), rate=t.get("rate"), amount=t.get("amount"),
                   tax_id=t.get("id", "iva"))
        for t in (d.get("taxes") or []) if isinstance(t, dict))
    deductions = tuple(
        CalcDeductionMap(base=x.get("base"), rate=x.get("rate"), amount=x.get("amount"),
                         ded_id=x.get("id", "ded"))
        for x in (d.get("deductions") or []) if isinstance(x, dict))
    return CalcContract(
        lines=lm, subtotal=d.get("subtotal"), discount=d.get("discount"),
        surcharge=d.get("surcharge"), retention=d.get("retention"),
        total=d.get("total"), taxes=taxes, deductions=deductions)


def load_calc_contract(
    doc_type: str | None, *, client: str = "default", config_root: str = "config",
) -> Optional[CalcContract]:
    """Lee ``config/<client>/vera_contracts.yml`` → ``<doc_type>.calc``. ``None`` si no
    hay fichero, doc_type o bloque ``calc`` (→ dimensión inerte). Cae a ``default``."""
    if not doc_type:
        return None
    import yaml
    for cli in (client, "default"):
        path = os.path.join(config_root, cli, "vera_contracts.yml")
        if not os.path.isfile(path):
            continue
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        block = (data.get(doc_type) or {}).get("calc") if isinstance(data, dict) else None
        if isinstance(block, dict):
            cc = calc_contract_from_dict(block)
            return None if cc.is_empty else cc
    return None


# ───────────────────────────── parseo de valores ───────────────────────────

def _first_magnitude(text: Any) -> Optional[Decimal]:
    """Primer monto canónico (Decimal exacto) del string, o None. Reusa el parser de VERA."""
    if text is None:
        return None
    s = str(text).replace("%", " ")
    for m in parse_numeric_mentions(s):
        if m.is_amount and m.magnitude is not None:
            return m.magnitude
    return None


def amount_to_cents(text: Any) -> Optional[Fraction]:
    """Importe (string es-AR/intl) → céntimos EXACTOS (Fraction), o None si no parsea."""
    mag = _first_magnitude(text)
    return None if mag is None else to_cents(Fraction(mag))


def rate_to_fraction(text: Any) -> Optional[Fraction]:
    """Tasa → Fraction (0.21). '21%'/'21' → 21/100; '0,21' → 0.21. None si no parsea."""
    if text is None:
        return None
    mag = _first_magnitude(text)
    if mag is None:
        return None
    frac = Fraction(mag)
    is_percent = "%" in str(text) or frac > 1
    return frac / 100 if is_percent else frac


def quantity_to_fraction(text: Any) -> Optional[Fraction]:
    """Cantidad → Fraction EXACTO SIN escala de céntimos ni heurística de %: '2' → 2,
    '1,5' → 3/2. None si no parsea."""
    mag = _first_magnitude(text)
    return None if mag is None else Fraction(mag)


# ───────────────────────────── acceso a la extracción ──────────────────────

def _ci_get(d: Any, key: str) -> Any:
    """Lookup case-insensitive de ``key`` en un dict (hojas directas)."""
    if not isinstance(d, dict):
        return None
    if key in d:
        return d[key]
    low = {str(k).lower(): v for k, v in d.items()}
    return low.get(key.lower())


# ───────────────────────────── adaptador ───────────────────────────────────

def invoice_from_extraction(extraction: dict[str, Any], cc: CalcContract) -> Invoice:
    """Mapea una extracción (header + array de líneas) a ``Invoice`` role-tagged.

    Observaciones que no parsean numéricamente se OMITEN (no se inventan ceros): el
    invariante que las necesita quedará ``INCOMPLETE``, no falsamente inconsistente.
    La procedencia (``provenance``) conserva la clave y el texto crudo para la fase
    de atribución futura.
    """
    def obs(oid: str, role: Role, raw: Any, kind: str = "amount") -> Optional[Obs]:
        if kind == "rate":
            val = rate_to_fraction(raw)
        elif kind == "qty":
            val = quantity_to_fraction(raw)
        else:
            val = amount_to_cents(raw)
        if val is None:
            return None
        return Obs(oid, role, val, text=None if raw is None else str(raw),
                   provenance={"raw": raw})

    lines: list[LineItem] = []
    if cc.lines is not None:
        arr = _ci_get(extraction, cc.lines.array)
        if isinstance(arr, list):
            for i, row in enumerate(arr):
                lines.append(LineItem(
                    line_id=str(i),
                    qty=obs(f"L{i}.qty", Role.QTY, _ci_get(row, cc.lines.qty), kind="qty"),
                    unit_price=obs(f"L{i}.price", Role.UNIT_PRICE, _ci_get(row, cc.lines.price)),
                    amount=obs(f"L{i}.amount", Role.LINE_AMOUNT, _ci_get(row, cc.lines.amount)),
                    discount=(obs(f"L{i}.disc", Role.LINE_DISCOUNT, _ci_get(row, cc.lines.discount))
                              if cc.lines.discount else None),
                ))

    taxes: list[TaxComponent] = []
    for j, tm in enumerate(cc.taxes):
        taxes.append(TaxComponent(
            tax_id=tm.tax_id or f"t{j}",
            base=obs(f"T{j}.base", Role.TAX_BASE, _ci_get(extraction, tm.base)) if tm.base else None,
            rate=obs(f"T{j}.rate", Role.TAX_RATE, _ci_get(extraction, tm.rate), kind="rate") if tm.rate else None,
            amount=obs(f"T{j}.amount", Role.TAX_AMOUNT, _ci_get(extraction, tm.amount)) if tm.amount else None,
        ))

    deductions: list[Deduction] = []
    for j, dm in enumerate(cc.deductions):
        deductions.append(Deduction(
            ded_id=dm.ded_id or f"d{j}",
            base=obs(f"D{j}.base", Role.TAX_BASE, _ci_get(extraction, dm.base)) if dm.base else None,
            rate=obs(f"D{j}.rate", Role.TAX_RATE, _ci_get(extraction, dm.rate), kind="rate") if dm.rate else None,
            amount=obs(f"D{j}.amount", Role.TAX_AMOUNT, _ci_get(extraction, dm.amount)) if dm.amount else None,
        ))

    return Invoice(
        lines=lines,
        subtotal=obs("subtotal", Role.SUBTOTAL, _ci_get(extraction, cc.subtotal)) if cc.subtotal else None,
        discount=obs("discount", Role.DISCOUNT, _ci_get(extraction, cc.discount)) if cc.discount else None,
        surcharge=obs("surcharge", Role.SURCHARGE, _ci_get(extraction, cc.surcharge)) if cc.surcharge else None,
        taxes=taxes,
        deductions=deductions,
        retention=obs("retention", Role.RETENTION, _ci_get(extraction, cc.retention)) if cc.retention else None,
        total=obs("total", Role.TOTAL, _ci_get(extraction, cc.total)) if cc.total else None,
    )


# ───────────────────────────── auto-detección ──────────────────────────────

import re as _re
import unicodedata as _ud


def _norm(s: Any) -> str:
    """Normaliza una clave: sin acentos, minúsculas, solo alfanumérico ('Precio Unit.'→'preciounit')."""
    t = _ud.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return _re.sub(r"[^a-z0-9]", "", t.lower())


# Alias normalizados por rol (orden = prioridad). 'amount' excluye 'total' (ambiguo).
_ALIASES = {
    "qty":      ["cantidad", "cant", "qty", "unidades", "cantidades", "cantidaduni"],
    "price":    ["preciounitario", "preciounit", "precio", "punitario", "valorunitario",
                 "importeunitario", "preciouni"],
    "amount":   ["importe", "subtotal", "monto", "importelinea", "total", "importetotal"],
    "subtotal": ["subtotal", "netogravado", "importeneto", "subtotalgravado", "neto"],
    "total":    ["total", "importetotal", "totalapagar", "netoapagar", "montototal",
                 "totalfinal", "totalfactura"],
    "iva":      ["iva", "importeiva", "montoiva", "impuestoiva", "iva21", "iva105"],
    "base":     ["baseimponible", "baseiva", "baseimponible21"],
    "rate":     ["alicuota", "alicuotaiva", "tasa", "tasaiva", "porcentajeiva"],
}


def _find_key(d: dict, role: str, used: set) -> Optional[str]:
    """Primera clave de ``d`` que matchea un alias del rol y no fue usada."""
    norm_map = {_norm(k): k for k in d}
    for alias in _ALIASES[role]:
        if alias in norm_map and norm_map[alias] not in used:
            return norm_map[alias]
    # match parcial (la clave CONTIENE el alias), para 'importe total' etc.
    for alias in _ALIASES[role]:
        for nk, k in norm_map.items():
            if alias in nk and k not in used:
                return k
    return None


def _find_items_array(extraction: dict) -> Optional[tuple[str, dict]]:
    """Encuentra (clave_array, primer_item) de un array de LÍNEAS: lista de dicts con al
    menos 2 roles de {qty, price, amount}."""
    for k, v in extraction.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            first = v[0]
            roles = sum(1 for r in ("qty", "price", "amount") if _find_key(first, r, set()))
            if roles >= 2:
                return k, first
    return None


_ACC_ALIASES = {
    "debit":       ["debe", "debito", "debit", "montodebe", "egreso", "retiro"],
    "credit":      ["haber", "credito", "credit", "montohaber", "ingreso", "deposito"],
    "account":     ["cuenta", "account", "concepto", "descripcion"],
    "assets":      ["activo", "totalactivo", "assets", "activototal"],
    "liabilities": ["pasivo", "totalpasivo", "liabilities", "pasivototal"],
    "equity":      ["patrimonioneto", "patrimonio", "capital", "equity", "pn"],
    "opening":     ["saldoinicial", "saldoanterior", "saldoprevio", "openingbalance"],
    "closing":     ["saldofinal", "saldoactual", "saldonuevo", "closingbalance"],
}


def _find_acc_key(d: dict, role: str, used: Optional[set] = None) -> Optional[str]:
    used = used or set()
    norm_map = {_norm(k): k for k in d}
    for alias in _ACC_ALIASES[role]:
        if alias in norm_map and norm_map[alias] not in used:
            return norm_map[alias]
    for alias in _ACC_ALIASES[role]:
        for nk, k in norm_map.items():
            if alias in nk and k not in used:
                return k
    return None


def infer_accounting(extraction: Any):
    """Infiere un ``AccountingDoc`` (asiento y/o balance) de la extracción, o ``None`` si no
    parece contable. Conservador: exige señal clara (debe/haber, o activo + pasivo/PN)."""
    from .accounting import AccountEntry, AccountingDoc, Balance, Journal, Statement
    if not isinstance(extraction, dict):
        return None

    # Resumen de cuenta: saldo inicial + saldo final (y, si hay, un array de movimientos
    # crédito/débito). Se detecta ANTES que el asiento porque sus saldos lo distinguen.
    statement = None
    ok_k = _find_acc_key(extraction, "opening")
    cl_k = _find_acc_key(extraction, "closing")
    if ok_k and cl_k:
        credits, debits = [], []
        for k, v in extraction.items():
            if isinstance(v, list) and v and all(isinstance(r, dict) for r in v):
                if any(_find_acc_key(r, "debit") or _find_acc_key(r, "credit") for r in v):
                    for i, row in enumerate(v):
                        ck = _find_acc_key(row, "credit")
                        dk = _find_acc_key(row, "debit")
                        cv = amount_to_cents(_ci_get(row, ck)) if ck else None
                        dv = amount_to_cents(_ci_get(row, dk)) if dk else None
                        if cv is not None:
                            credits.append(Obs(f"C{i}", Role.CREDIT, cv))
                        if dv is not None:
                            debits.append(Obs(f"B{i}", Role.DEBIT, dv))
                    break
        ov = amount_to_cents(_ci_get(extraction, ok_k))
        clv = amount_to_cents(_ci_get(extraction, cl_k))
        statement = Statement(
            opening=Obs("opening", Role.SUBTOTAL, ov) if ov is not None else None,
            closing=Obs("closing", Role.TOTAL, clv) if clv is not None else None,
            credits=credits, debits=debits)

    # Asiento: array de líneas donde alguna trae debe/haber. Las claves se buscan POR FILA
    # (una fila puede tener solo debe y otra solo haber). Si ya se detectó un resumen, sus
    # movimientos NO son un asiento aparte.
    journal = None
    for k, v in ((extraction.items()) if statement is None else ()):
        if not (isinstance(v, list) and v and all(isinstance(r, dict) for r in v)):
            continue
        if not any(_find_acc_key(r, "debit") or _find_acc_key(r, "credit") for r in v):
            continue
        entries = []
        for i, row in enumerate(v):
            dk = _find_acc_key(row, "debit")
            ck = _find_acc_key(row, "credit")
            ak = _find_acc_key(row, "account")
            dv = amount_to_cents(_ci_get(row, dk)) if dk else None
            cv = amount_to_cents(_ci_get(row, ck)) if ck else None
            entries.append(AccountEntry(
                account=str(_ci_get(row, ak)) if ak else str(i),
                debit=Obs(f"D{i}", Role.DEBIT, dv) if dv is not None else None,
                credit=Obs(f"H{i}", Role.CREDIT, cv) if cv is not None else None))
        if entries:
            journal = Journal(entries)
        break

    # Balance: activo + (pasivo y/o patrimonio neto).
    balance = None
    used: set = set()
    ak = _find_acc_key(extraction, "assets", used)
    if ak:
        used.add(ak)
    lk = _find_acc_key(extraction, "liabilities", used)
    if lk:
        used.add(lk)
    ek = _find_acc_key(extraction, "equity", used)
    if ak and (lk or ek):
        av = amount_to_cents(_ci_get(extraction, ak))
        lv = amount_to_cents(_ci_get(extraction, lk)) if lk else None
        ev = amount_to_cents(_ci_get(extraction, ek)) if ek else None
        balance = Balance(
            assets=Obs("assets", Role.ASSETS, av) if av is not None else None,
            liabilities=Obs("liabilities", Role.LIABILITIES, lv) if lv is not None else None,
            equity=Obs("equity", Role.EQUITY, ev) if ev is not None else None)

    if journal is None and balance is None and statement is None:
        return None
    return AccountingDoc(journal=journal, balance=balance, statement=statement)


def infer_calc_contract(extraction: Any) -> Optional[CalcContract]:
    """Infiere un ``CalcContract`` desde la extracción, sin contrato configurado. Devuelve
    ``None`` si no parece una factura (no hay total + al menos líneas o subtotal/IVA).
    Conservador: solo arma el contrato si hay señal clara. Pensado para modo SOMBRA."""
    if not isinstance(extraction, dict):
        return None
    used: set = set()
    total_k = _find_key(extraction, "total", used)
    if not total_k:
        return None
    used.add(total_k)

    lines = None
    arr = _find_items_array(extraction)
    if arr is not None:
        array_key, first = arr
        qk = _find_key(first, "qty", set())
        pk = _find_key(first, "price", set())
        ak = _find_key(first, "amount", set())
        if ak:  # el importe de línea es lo mínimo indispensable
            lines = CalcLineMap(array=array_key, qty=qk or "", price=pk or "", amount=ak)

    sub_k = _find_key(extraction, "subtotal", used)
    if sub_k:
        used.add(sub_k)
    iva_k = _find_key(extraction, "iva", used)
    rate_k = _find_key(extraction, "rate", used) if iva_k else None
    base_k = _find_key(extraction, "base", used) if iva_k else None
    taxes = ()
    if iva_k:
        # base del impuesto: la propia (base imponible) o, si no hay, el neto/subtotal
        # (caso libro IVA: neto gravado × alícuota = IVA). Con la tasa se chequea base×tasa.
        taxes = (CalcTaxMap(base=base_k or sub_k, rate=rate_k, amount=iva_k, tax_id="iva"),)

    cc = CalcContract(lines=lines, subtotal=sub_k, total=total_k, taxes=taxes)
    # exige señal real: total + (líneas o subtotal o iva)
    if cc.lines is None and cc.subtotal is None and not cc.taxes:
        return None
    return cc


# ───────────────────────────── flags (opt-in) ──────────────────────────────

def calc_enabled() -> bool:
    """True si la dimensión de cálculo está activa por env: ``VERA_CALC=on``.
    Default OFF → inerte, cero cambio de comportamiento."""
    return os.getenv("VERA_CALC", "off").strip().lower() in ("on", "1", "true")


def calc_mode() -> str:
    """``shadow`` (default, solo verifica y loguea) | ``authoritative`` (adjunta el
    veredicto y bloquea ante inconsistencia demostrada). Env ``VERA_CALC_MODE``."""
    return os.getenv("VERA_CALC_MODE", "shadow").strip().lower()


# ───────────────────────────── entry-point ─────────────────────────────────

def verify_calc(
    extraction: dict[str, Any],
    *,
    doc_type: str | None = None,
    client: str = "default",
    config_root: str = "config",
    calc_contract: Optional[CalcContract] = None,
    autodetect: bool = True,
    line_tolerance_cents: int = 1,
    tax_tolerance_cents: int = 2,
    total_tolerance_cents: int = 2,
) -> Optional[CalcResult]:
    """Verifica la consistencia de cálculo de una extracción. Opt-in e inerte:
    devuelve ``None`` si no hay contrato configurado NI se pudo inferir uno de la
    extracción (``autodetect``). Mirror de ``integration.verify_extraction``."""
    cc = calc_contract or load_calc_contract(
        doc_type, client=client, config_root=config_root)
    if cc is not None:
        inv = invoice_from_extraction(extraction, cc)
        return check_calc_consistency(
            inv, line_tolerance_cents=line_tolerance_cents,
            tax_tolerance_cents=tax_tolerance_cents,
            total_tolerance_cents=total_tolerance_cents)
    if autodetect:
        # Contable primero (debe/haber o activo+pasivo son señal inequívoca); si no,
        # factura (líneas + total).
        acc = infer_accounting(extraction)
        if acc is not None:
            from .accounting import check_accounting
            return check_accounting(acc, tolerance_cents=total_tolerance_cents)
        cc = infer_calc_contract(extraction)
        if cc is not None:
            inv = invoice_from_extraction(extraction, cc)
            return check_calc_consistency(
                inv, line_tolerance_cents=line_tolerance_cents,
                tax_tolerance_cents=tax_tolerance_cents,
                total_tolerance_cents=total_tolerance_cents)
    return None


__all__ = [
    "CalcLineMap", "CalcTaxMap", "CalcDeductionMap", "CalcContract",
    "calc_contract_from_dict", "load_calc_contract",
    "invoice_from_extraction", "amount_to_cents", "rate_to_fraction",
    "quantity_to_fraction", "verify_calc", "calc_enabled", "calc_mode",
    "infer_calc_contract", "infer_accounting",
]
