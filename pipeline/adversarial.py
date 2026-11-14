# Autor: @Raven
"""Fallos ADVERSARIALES para estresar a VERA más allá de sus dos patrones base (sección 5).

Diez familias, cada una con etiqueta de verdad. A diferencia de ``faults.py`` (corrupción que
rompe aritmética / alucinación de valor ausente — diseñadas para activar las reglas), estas
buscan los BORDES: valores aritméticamente consistentes, identificadores plausibles, valores
que existen en OTRO documento, líneas sustituidas/omitidas, y campos cuya evidencia OCR está
degradada. El objetivo es MEDIR qué detecta VERA de verdad, no demostrar 100%.

Cada generador recibe ``(inv, rng, pool)`` y devuelve ``AdvCase`` con:
  - ``extraction``: la extracción (posiblemente corrupta) que verá VERA.
  - ``ocr_text``   : la fuente OCR (algunas familias la degradan).
  - ``family``     : nombre de la familia (o ``clean``).
  - ``bad``        : True si es un error que VERA DEBERÍA no-verificar.

``pool`` = lista de ``truth`` de OTRAS facturas del corpus (para familias "valor de otro doc").
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass
from typing import Any, Callable

from framework.vera.calc_contract import amount_to_cents


def _fmt_cents(cents) -> str:
    cents = int(cents)
    neg = cents < 0
    cents = abs(cents)
    pesos, cc = divmod(cents, 100)
    return f"{'-' if neg else ''}$ {pesos:,}".replace(",", ".") + f",{cc:02d}"


def _cuit_check_digit(d10: str) -> int:
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(a) * b for a, b in zip(d10, weights))
    r = 11 - (s % 11)
    return 0 if r == 11 else (9 if r == 10 else r)


def _valid_cuit(rng: random.Random) -> str:
    body = f"20{rng.randint(10_000_000, 44_999_999)}"   # prefijo 20 + DNI plausible
    return f"{body[:2]}-{body[2:]}-{_cuit_check_digit(body)}"


@dataclass
class AdvCase:
    invoice_id: str
    extraction: dict[str, Any]
    ocr_text: str                 # fuente INDEPENDIENTE (OCR degradado que vio el reconstructor)
    family: str
    bad: bool
    clean_text: str = ""          # fuente PRIVILEGIADA (render/pdftotext limpio) — para comparar


# ── las 10 familias ───────────────────────────────────────────────────────────
def _scale_amounts(e: dict, k_num: int, k_den: int) -> None:
    """Escala todos los montos por k_num/k_den preservando TODOS los invariantes."""
    def sc(v):
        c = amount_to_cents(v)
        return _fmt_cents(c * k_num // k_den) if c is not None else v
    for it in e.get("items", []):
        it["importe"] = sc(it["importe"])
        it["precio_unitario"] = sc(it["precio_unitario"])
    for key in ("subtotal", "base_imponible", "importe_iva", "total",
                "Subtotal", "Importe IVA", "Total a pagar"):
        if key in e:
            e[key] = sc(e[key])


def f1_monto_inventado_consistente(inv, rng, pool):
    """Total/IVA/alícuota fabricados PERO subtotal+IVA=total y base*tasa=IVA siguen cuadrando
    (subtotal intacto → sigue anclando). Solo la cobertura puede cazar el total/IVA inventados."""
    e = copy.deepcopy(inv["truth"])
    sub = amount_to_cents(e.get("subtotal"))
    if sub is None:
        return None
    new_total = sub + rng.randint(50_000_00, 150_000_00)
    new_iva = new_total - sub
    e["total"] = e["Total a pagar"] = _fmt_cents(new_total)
    e["importe_iva"] = e["Importe IVA"] = _fmt_cents(new_iva)
    rate = round(new_iva * 100 / sub, 2)
    e["alicuota_iva"] = e["Alícuota IVA"] = f"{rate}%".replace(".", ",")
    return e, inv["ocr_text"]


def f2_cuit_valido_incorrecto(inv, rng, pool):
    """CUIT con checksum VÁLIDO pero que no es el del documento → plausible, no anclado."""
    e = copy.deepcopy(inv["truth"])
    new = _valid_cuit(rng)
    while new == e.get("CUIT emisor"):
        new = _valid_cuit(rng)
    e["CUIT emisor"] = new
    return e, inv["ocr_text"]


def f3_fecha_valida_inventada(inv, rng, pool):
    e = copy.deepcopy(inv["truth"])
    new = f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2026"
    while new == e.get("Fecha de emisión"):
        new = f"{rng.randint(1,28):02d}/{rng.randint(1,12):02d}/2026"
    e["Fecha de emisión"] = new
    return e, inv["ocr_text"]


def f4_proveedor_existente_incorrecto(inv, rng, pool):
    """CUIT de OTRA factura del corpus (existe, pero no es el de este documento)."""
    e = copy.deepcopy(inv["truth"])
    others = [t.get("CUIT emisor") for t in pool
              if t.get("CUIT emisor") and t.get("CUIT emisor") != e.get("CUIT emisor")]
    if not others:
        return None
    e["CUIT emisor"] = rng.choice(others)
    return e, inv["ocr_text"]


def f5_linea_sustituida(inv, rng, pool):
    """Reemplaza una línea por otra y RECALCULA subtotal/IVA/total → aritmética consistente;
    los montos nuevos no están en el OCR."""
    e = copy.deepcopy(inv["truth"])
    items = e.get("items")
    if not items:
        return None
    i = rng.randrange(len(items))
    qty = rng.randint(1, 3)
    price_c = rng.randint(50_00, 250_000_00)
    items[i] = {"cantidad": str(qty), "precio_unitario": _fmt_cents(price_c),
                "importe": _fmt_cents(qty * price_c)}
    _recompute(e)
    return e, inv["ocr_text"]


def f6_linea_omitida(inv, rng, pool):
    """Omite una línea pero DEJA subtotal/total originales → Σlíneas < subtotal (rompe cálculo)."""
    e = copy.deepcopy(inv["truth"])
    items = e.get("items")
    if not items or len(items) < 2:
        return None
    items.pop(rng.randrange(len(items)))
    return e, inv["ocr_text"]


def f7_escalado_consistente(inv, rng, pool):
    """Escala TODOS los montos (incluido subtotal) por un factor → invariantes intactos, todo
    inventado. Ni el cálculo lo caza; solo la cobertura (ningún monto escalado está en el OCR)."""
    e = copy.deepcopy(inv["truth"])
    _scale_amounts(e, rng.choice([3, 7, 11]), 2)
    return e, inv["ocr_text"]


def f8_campo_con_evidencia_degradada(inv, rng, pool):
    """Corrompe el total Y degrada su línea en el OCR (confusión de dígitos) → la evidencia
    también está dañada. Mide si VERA lo marca INVENTED o REVIEW (no-confirmable)."""
    e = copy.deepcopy(inv["truth"])
    cur = amount_to_cents(e.get("total"))
    if cur is None:
        return None
    e["total"] = e["Total a pagar"] = _fmt_cents(cur + rng.randint(100_00, 900_00))
    # degradar la línea del total en el OCR (0→O, 1→l, 5→S, …)
    conf = {"0": "O", "1": "l", "5": "S", "3": "E", "8": "B"}
    def garble(line):
        return re.sub(r"\d", lambda m: conf.get(m.group(0), m.group(0)) if rng.random() < 0.5
                      else m.group(0), line)
    ocr = "\n".join(garble(l) if re.search(r"total a pagar", l, re.I) else l
                    for l in inv["ocr_text"].splitlines())
    return e, ocr


def f9_rellenado_en_vez_de_abstener(inv, rng, pool):
    """Simula el régimen de precondición violada (Prop. 1): el campo NO está en el OCR (se borra
    su línea) y el modelo lo RELLENA con un valor plausible en vez de abstenerse ([UNRECOVERABLE])."""
    e = copy.deepcopy(inv["truth"])
    # borrar la línea del CUIT del OCR (evidencia ausente)
    ocr = "\n".join(l for l in inv["ocr_text"].splitlines() if "CUIT" not in l)
    # el "reconstructor" rellena igual con un CUIT plausible (no abstiene)
    e["CUIT emisor"] = _valid_cuit(rng)
    return e, ocr


def f10_valor_de_otro_documento(inv, rng, pool):
    """Copia subtotal/IVA/total de OTRA factura del corpus → internamente consistente (viene de
    una factura real) pero pertenece a otro documento; no está en ESTE OCR."""
    e = copy.deepcopy(inv["truth"])
    donors = [t for t in pool if t.get("total") and t.get("total") != e.get("total")]
    if not donors:
        return None
    d = rng.choice(donors)
    for k in ("subtotal", "base_imponible", "importe_iva", "total"):
        if k in d:
            e[k] = d[k]
    e["Subtotal"] = d.get("Subtotal", e.get("Subtotal"))
    e["Importe IVA"] = d.get("Importe IVA", e.get("Importe IVA"))
    e["Total a pagar"] = d.get("Total a pagar", e.get("Total a pagar"))
    return e, inv["ocr_text"]


def _recompute(e: dict) -> None:
    """Recalcula subtotal/IVA/total/base desde los items (mantiene invariantes)."""
    sub = sum(amount_to_cents(it["importe"]) or 0 for it in e.get("items", []))
    iva = round(sub * 21 / 100)
    e["subtotal"] = e["base_imponible"] = e["Subtotal"] = _fmt_cents(sub)
    e["importe_iva"] = e["Importe IVA"] = _fmt_cents(iva)
    e["total"] = e["Total a pagar"] = _fmt_cents(sub + iva)


FAMILIES: dict[str, Callable] = {
    "monto_inventado_consistente": f1_monto_inventado_consistente,
    "cuit_valido_incorrecto": f2_cuit_valido_incorrecto,
    "fecha_valida_inventada": f3_fecha_valida_inventada,
    "proveedor_existente_incorrecto": f4_proveedor_existente_incorrecto,
    "linea_sustituida": f5_linea_sustituida,
    "linea_omitida": f6_linea_omitida,
    "escalado_consistente": f7_escalado_consistente,
    "campo_con_evidencia_degradada": f8_campo_con_evidencia_degradada,
    "rellenado_en_vez_de_abstener": f9_rellenado_en_vez_de_abstener,
    "valor_de_otro_documento": f10_valor_de_otro_documento,
}


def build_adversarial_cases(invoices: list[dict], *, seed: int = 20261113,
                            clean_rate: float = 0.35) -> list[AdvCase]:
    """Para cada factura: con prob ``clean_rate`` queda LIMPIA (extracción = verdad, para medir
    falsos rechazos); si no, se le aplica una familia adversarial (round-robin determinista)."""
    rng = random.Random(seed)
    truths = [inv["truth"] for inv in invoices]
    fam_names = list(FAMILIES)
    cases: list[AdvCase] = []
    fam_i = 0
    for inv in invoices:
        pool = [t for t in truths if t is not inv["truth"]]
        clean = inv.get("clean_text", inv["ocr_text"])
        if rng.random() < clean_rate:
            cases.append(AdvCase(inv["invoice_id"], copy.deepcopy(inv["truth"]),
                                 inv["ocr_text"], "clean", bad=False, clean_text=clean))
            continue
        # elegir la siguiente familia aplicable
        for _ in range(len(fam_names)):
            fam = fam_names[fam_i % len(fam_names)]
            fam_i += 1
            res = FAMILIES[fam](inv, rng, pool)
            if res is not None:
                ext, ocr = res
                cases.append(AdvCase(inv["invoice_id"], ext, ocr, fam, bad=True,
                                     clean_text=clean))
                break
    return cases
