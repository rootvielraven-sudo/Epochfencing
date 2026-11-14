# Autor: @Raven
"""Generación del corpus de facturas (sintéticas, es-AR) para los experimentos.

Cada factura trae:
  - ``truth``: extracción correcta, con invariantes de cálculo satisfechos
               (cantidad*precio=importe, Σimporte=subtotal, base*tasa=IVA, subtotal+IVA=total).
  - ``ocr_text``: la misma factura renderizada como texto y degradada con ruido OCR
                  (lo único que ven el reconstructor y VERA).

Determinista por ``seed`` → el corpus se puede regenerar idéntico (necesario para comparar
el PDF con y sin fallos).
"""
from __future__ import annotations

import random
from typing import Any

from framework.vera.eval.ocr_noise import apply_noise

# Claves de cálculo (nivel extracción) que lee verify_calc para el doc_type 'factura'.
# Deben coincidir con el bloque `calc` de config/default/vera_contracts.yml.

_IVA_RATE = 21  # %


def _cuit_check_digit(d10: str) -> int:
    weights = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(a) * b for a, b in zip(d10, weights))
    r = 11 - (s % 11)
    return 0 if r == 11 else (9 if r == 10 else r)


def _valid_cuit(rng: random.Random) -> str:
    """CUIT con dígito verificador VÁLIDO (si no, VERA lo marca REVIEW por checksum)."""
    body = f"20{rng.randint(10_000_000, 44_999_999)}"   # prefijo 20 (persona) + DNI
    return f"{body[:2]}-{body[2:]}-{_cuit_check_digit(body)}"


def _fmt(cents: int) -> str:
    """Formatea centavos como importe es-AR: 1234556 -> '$ 12.345,56'."""
    neg = cents < 0
    cents = abs(cents)
    pesos, cc = divmod(cents, 100)
    s = f"{pesos:,}".replace(",", ".")
    return f"{'-' if neg else ''}$ {s},{cc:02d}"


def _make_one(rng: random.Random, idx: int) -> dict[str, Any]:
    n_lines = rng.randint(2, 5)
    items = []
    subtotal = 0
    for _ in range(n_lines):
        qty = rng.randint(1, 3)
        price_c = rng.randint(50_00, 250_000_00)       # 50,00 .. 250.000,00
        amount_c = qty * price_c
        subtotal += amount_c
        items.append({
            "cantidad": str(qty),
            "precio_unitario": _fmt(price_c),
            "importe": _fmt(amount_c),
        })
    iva_c = round(subtotal * _IVA_RATE / 100)
    total_c = subtotal + iva_c

    comprobante = f"{rng.randint(1, 9999):08d}"
    cuit = _valid_cuit(rng)
    dd, mm, yy = rng.randint(1, 28), rng.randint(1, 12), 2026
    fecha = f"{dd:02d}/{mm:02d}/{yy}"

    truth: dict[str, Any] = {
        # Cabecera (coverage, por nombre/alias del contrato):
        "Comprobante": comprobante,
        "CUIT emisor": cuit,
        "Fecha de emisión": fecha,
        "Total a pagar": _fmt(total_c),
        "Subtotal": _fmt(subtotal),
        "Importe IVA": _fmt(iva_c),
        "Alícuota IVA": f"{_IVA_RATE}%",
        # Cálculo (nivel extracción, lo que lee verify_calc):
        "items": items,
        "subtotal": _fmt(subtotal),
        "base_imponible": _fmt(subtotal),
        "alicuota_iva": f"{_IVA_RATE}%",
        "importe_iva": _fmt(iva_c),
        "total": _fmt(total_c),
    }
    return truth


def _render_ocr(truth: dict[str, Any]) -> str:
    lines = ["FACTURA B", f"Comprobante: {truth['Comprobante']}",
             f"CUIT emisor: {truth['CUIT emisor']}",
             f"Fecha de emisión: {truth['Fecha de emisión']}", "", "Detalle:"]
    for it in truth["items"]:
        lines.append(f"  {it['cantidad']} x {it['precio_unitario']}  =  {it['importe']}")
    lines += [
        "",
        f"Subtotal: {truth['Subtotal']}",
        f"Base imponible: {truth['base_imponible']}",
        f"Alícuota IVA: {truth['Alícuota IVA']}",
        f"Importe IVA: {truth['Importe IVA']}",
        f"Total a pagar: {truth['Total a pagar']}",
    ]
    return "\n".join(lines)


def generate_corpus(
    n: int, *, seed: int = 20261112, noise_tier: str | None = "degraded",
) -> list[dict[str, Any]]:
    """Genera ``n`` facturas. ``noise_tier`` ∈ {None, 'clean', 'degraded', 'poor'} (ruido OCR).

    Devuelve lista de dicts ``{invoice_id, ocr_text, truth}`` (compatibles con model.Invoice).
    """
    rng = random.Random(seed)
    out = []
    for i in range(n):
        truth = _make_one(rng, i)
        clean = _render_ocr(truth)
        if noise_tier:
            ocr = apply_noise(clean, noise_tier, rng)
        else:
            ocr = clean
        out.append({
            "invoice_id": f"F{i:05d}",
            "ocr_text": ocr,            # OCR degradado: ENTRADA del reconstructor
            "clean_text": clean,        # texto reconstruido correcto: FUENTE de verificación VERA
            "truth": truth,
        })
    return out
