# Autor: @Raven
"""Generación del PDF final: solo lo que VERA avala entra como VERIFICADO; lo demás entra
con su OCR crudo y MARCADO (DEGRADED). La compuerta no bloquea la corrida.
"""
from __future__ import annotations

from typing import Any

from .model import InvoiceState, Record


def build_pdf(records: list[Record], invoices: dict[str, Any], out_path: str) -> dict[str, int]:
    """Escribe el PDF con una sección por factura. Devuelve conteos {verified, degraded}."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(out_path, pagesize=A4, title="Facturas reconstruidas")
    flow = [Paragraph("Facturas reconstruidas — compuerta VERA", styles["Title"]),
            Spacer(1, 6 * mm)]
    counts = {"verified": 0, "degraded": 0}

    for rec in sorted(records, key=lambda r: r.invoice_id):
        verified = rec.state is InvoiceState.VERIFIED
        counts["verified" if verified else "degraded"] += 1
        badge = "✔ VERIFICADA" if verified else "⚠ DEGRADED (sin verificar)"
        color = colors.HexColor("#1b7f3b") if verified else colors.HexColor("#b25f00")
        reasons = ", ".join(rec.vera.get("reasons", [])) or "—"

        flow.append(Paragraph(f"<b>{rec.invoice_id}</b> &nbsp; "
                              f'<font color="{color.hexval()}">{badge}</font>',
                              styles["Heading3"]))
        if verified:
            e = rec.extraction
            rows = [["Campo", "Valor"]]
            for k in ("Comprobante", "CUIT emisor", "Fecha de emisión",
                      "Subtotal", "Importe IVA", "Total a pagar"):
                rows.append([k, str(e.get(k, "—"))])
            t = Table(rows, colWidths=[55 * mm, 90 * mm])
            t.setStyle(TableStyle([
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef3ee")),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
            ]))
            flow.append(t)
        else:
            flow.append(Paragraph(f"<i>Motivo: {reasons}. Se adjunta el OCR crudo:</i>",
                                  styles["Italic"]))
            ocr = invoices.get(rec.invoice_id, {}).get("ocr_text", "(OCR no disponible)")
            flow.append(Paragraph("<font face='Courier' size=7>"
                                  + ocr.replace("\n", "<br/>").replace("&", "&amp;")
                                  + "</font>", styles["Normal"]))
        flow.append(Spacer(1, 4 * mm))

    doc.build(flow)
    return counts
