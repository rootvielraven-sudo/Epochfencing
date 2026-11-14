# Autor: @Raven
"""Corpus de facturas REALES (PDFs de /home/athenas/Desktop/datos-demo).

Cada factura real se convierte al mismo formato que ``corpus.generate_corpus``:
  - ``clean_text``: texto del PDF (``pdftotext -layout``) → fuente de verificación de VERA.
  - ``ocr_text``:   ese texto degradado con ruido OCR sintético (simula PaddleOCR 100 DPI).
  - ``truth``:      extracción parseada (cabecera + líneas cuando el layout lo permite).

Los números son REALES (transcriptos por el parser del propio PDF), así que la verdad de
referencia es el documento mismo. Las 3 facturas con transcripción manual completa
(``calc_eval.inject_real``: F1/F2/F3, con líneas e IVA) se usan tal cual para la dimensión de
CÁLCULO; el resto aporta cabecera para la dimensión de COBERTURA.

Requiere ``pdftotext`` (poppler-utils).
"""
from __future__ import annotations

import glob
import os
import re
import subprocess
from typing import Any

from framework.vera.eval.ocr_noise import apply_noise

DEMO_DIR = "/home/athenas/Desktop/datos-demo"

_AMOUNT = r"\$?\s*([0-9][0-9.]*,[0-9]{2})"


def extract_text(pdf_path: str) -> str:
    """Texto del PDF con layout preservado (proxy del OCR del documento)."""
    out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                         capture_output=True, timeout=30)
    return out.stdout.decode("utf-8", errors="replace")


def _find(pat: str, text: str, flags=re.I) -> str | None:
    m = re.search(pat, text, flags)
    return m.group(1).strip() if m else None


def parse_invoice(text: str) -> dict[str, Any] | None:
    """Parsea los campos de cabecera (y líneas si se puede) de una factura CompraGamer/Newton."""
    total = _find(r"TOTAL:?\s*" + _AMOUNT, text)
    if not total:
        return None                       # sin total no es utilizable
    nro = _find(r"Nro Factura:?\s*([0-9]+)", text)
    comprobante = (nro or "").strip()     # como aparece en el texto (sin la serie, para que ancle)
    cuit = _find(r"CUIT:?\s*([0-9]{2}-?[0-9]{8}-?[0-9])", text)
    if cuit and "-" not in cuit:
        cuit = f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}"
    fecha = _find(r"Fecha:?\s*([0-3]?[0-9][-/][0-1]?[0-9][-/][0-9]{2,4})", text)
    razon = _find(r"\n\s*([A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ .,&]+S\.?R\.?L\.?|[A-ZÁÉÍÓÚÑ .,&]+S\.?A\.?)\b", text)

    # Líneas: filas que terminan en dos importes (precio_unit, subtotal).
    from framework.vera.calc_contract import amount_to_cents
    items = []
    for line in text.splitlines():
        m = re.search(_AMOUNT + r"\s{2,}" + _AMOUNT + r"\s*$", line)
        if not m:
            continue
        precio, sub = m.group(1), m.group(2)
        pc, sc = amount_to_cents(precio), amount_to_cents(sub)
        if not pc or not sc or pc == 0:
            continue
        qty = round(float(sc) / float(pc))
        if qty >= 1 and abs(qty * float(pc) - float(sc)) < 1:   # cuadra cant*precio=subtotal
            items.append({"cantidad": str(qty), "precio_unitario": precio.strip(),
                          "importe": sub.strip()})

    truth: dict[str, Any] = {
        "Comprobante": comprobante,
        "CUIT emisor": cuit or "",
        "Fecha de emisión": fecha or "",
        "Total a pagar": f"${total}",
        "total": f"${total}",
    }
    if razon:
        truth["Razón Social"] = razon
    # Adjuntar líneas para la dimensión de CÁLCULO SOLO si Σlíneas == total (Factura B sin
    # IVA discriminado, invariante Σ=total). Si hay IVA/envío/descuento, Σ≠total y el parser
    # no reconstruye el desglose → se omiten las líneas (cálculo queda INCOMPLETE, no se
    # marca) y la factura se verifica por COBERTURA. Evita falsos positivos de cálculo.
    total_c = amount_to_cents(total)
    sum_c = sum((amount_to_cents(it["importe"]) or 0) for it in items)
    if items and total_c is not None and abs(float(sum_c) - float(total_c)) < 1:
        truth["items"] = items
    return truth


def _merge_transcribed(truth: dict[str, Any], comprobante: str) -> dict[str, Any]:
    """Si la factura es una de las 3 con transcripción manual completa (líneas + IVA reales),
    usa esa estructura de cálculo (más fiel que el parser de líneas)."""
    try:
        from framework.vera.calc_eval.inject_real import REAL
    except Exception:
        return truth
    nro = comprobante.split("-")[-1].lstrip("0")
    for f in REAL:
        fnro = re.sub(r"\D", "", f["name"]).lstrip("0")
        if nro and nro in fnro:
            merged = dict(truth)
            merged.update(f["extraction"])         # líneas + IVA + total reales transcriptos
            merged["Total a pagar"] = f["extraction"].get("total", truth["Total a pagar"])
            return merged
    return truth


def load_real_corpus(
    source_dir: str | None = None, *, limit: int | None = None,
    noise_tier: str | None = "degraded", seed: int = 20261112,
    subdirs: tuple[str, ...] = ("CompraGamer", "facturas_vera"),
) -> list[dict[str, Any]]:
    """Carga facturas reales. Devuelve dicts {invoice_id, ocr_text, clean_text, truth}."""
    import random
    base = source_dir or DEMO_DIR
    pdfs: list[str] = []
    for sd in subdirs:
        pdfs += sorted(glob.glob(os.path.join(base, sd, "factura_*.pdf")))
    # dedup por nombre de archivo (facturas_vera repite algunas de CompraGamer)
    seen, uniq = set(), []
    for p in pdfs:
        b = os.path.basename(p)
        if b not in seen:
            seen.add(b); uniq.append(p)
    rng = random.Random(seed)
    out = []
    for p in uniq:
        if limit and len(out) >= limit:
            break
        text = extract_text(p)
        truth = parse_invoice(text)
        if truth is None:
            continue
        ocr = apply_noise(text, noise_tier, rng) if noise_tier else text
        out.append({
            "invoice_id": os.path.splitext(os.path.basename(p))[0],
            "ocr_text": ocr,
            "clean_text": text,
            "truth": truth,
            "source_pdf": p,
        })
    return out
