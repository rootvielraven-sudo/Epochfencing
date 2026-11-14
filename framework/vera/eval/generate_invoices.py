# Autor: @Raven
"""STEP 1 — Generador de facturas sintéticas DIFÍCILES (ground truth plantado).

Escribe, por factura, DOS archivos:
  invoice_XXXX.txt         → lo que el LLM lee (la fuente S; NO contiene las claves-slug)
  invoice_XXXX.truth.json  → el valor CORRECTO de cada campo de precisión (ground truth)

Reglas: seed fijo (logueado), CUIT con checksum válido (coincide con VERA `_cuit_ok`),
formatos numéricos mezclados (1.234,56 vs 1,234.56), dos CUITs con la MISMA etiqueta
(trampa de migración de campo), tasa de IVA vs monto de IVA, valores cerca de saltos de
línea. La dificultad es a propósito: facturas triviales no enseñan nada.

El generador NUNCA produce la "extracción" — solo la fuente y la verdad. La extracción la
hace el LLM en run_eval.py.

Uso:  python -m framework.vera.eval.generate_invoices --n 100 --seed 20260703 --out data/invoices
"""
from __future__ import annotations

import argparse
import json
import os
import random

# ── CUIT con checksum válido (idéntico a VERA _cuit_ok) ───────────────────────
_W = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
_PREFIX = ["20", "23", "24", "27", "30", "33", "34"]


def _cuit(rng: random.Random) -> str:
    pre = rng.choice(_PREFIX)
    mid = "".join(str(rng.randint(0, 9)) for _ in range(8))
    base = pre + mid
    s = sum(int(base[i]) * _W[i] for i in range(10))
    dv = 11 - (s % 11)
    dv = 0 if dv == 11 else (9 if dv == 10 else dv)
    return f"{pre}-{mid}-{dv}"


# ── Formato numérico (dos locales) ────────────────────────────────────────────
def _fmt_amount(value: float, locale: str) -> str:
    """value con 2 decimales. locale 'ar' → 1.234.567,89 ; 'us' → 1,234,567.89."""
    ent, dec = f"{value:.2f}".split(".")
    neg = ent.startswith("-")
    ent = ent.lstrip("-")
    groups = []
    while len(ent) > 3:
        groups.insert(0, ent[-3:]); ent = ent[:-3]
    groups.insert(0, ent)
    if locale == "ar":
        body = ".".join(groups) + "," + dec
    else:
        body = ",".join(groups) + "." + dec
    return ("-" if neg else "") + body


_MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
          "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fmt_date(y: int, m: int, d: int, style: int) -> str:
    if style == 0:
        return f"{d:02d}/{m:02d}/{y}"
    if style == 1:
        return f"{y}-{m:02d}-{d:02d}"
    if style == 2:
        return f"{d} de {_MESES[m-1]} de {y}"
    return f"{d:02d}-{m:02d}-{y}"


_CONCEPTOS = ["Servicio de consultoría", "Licencia de software", "Soporte técnico",
              "Mantenimiento mensual", "Desarrollo a medida", "Hosting anual",
              "Capacitación", "Auditoría", "Integración API", "Migración de datos"]
_TOTAL_LABELS = ["Total a pagar", "TOTAL", "Importe total", "Total"]
_COMP_LABELS = ["Comprobante Nº", "Nro comprobante", "Número", "Comprobante"]
_FECHA_LABELS = ["Fecha de emisión", "Fecha"]


# Relleno realista (boilerplate) para ALARGAR el documento → el LLM se "ahoga" (lost-in-the-middle).
_FILLER = [
    "Condiciones de pago: el pago deberá efectuarse dentro de los treinta (30) días corridos de la fecha de emisión, mediante transferencia bancaria a la cuenta indicada al pie del comprobante.",
    "La presente factura reviste el carácter de título ejecutivo conforme la legislación vigente; su falta de pago en término devengará los intereses moratorios y punitorios que correspondan.",
    "Los importes consignados se expresan en pesos argentinos e incluyen los tributos aplicables, salvo que se indique expresamente lo contrario en el detalle de los conceptos facturados.",
    "El comprobante fue emitido electrónicamente y su validez puede verificarse en el portal del organismo fiscal mediante el código de autorización electrónico correspondiente a la operación.",
    "Toda observación o reclamo relativo a los bienes o servicios facturados deberá formularse por escrito dentro de los cinco (5) días hábiles de recibida la mercadería o prestado el servicio.",
    "El presente documento no habilita el cómputo de crédito fiscal cuando el receptor revista la condición de consumidor final de acuerdo con la normativa impositiva aplicable al caso.",
    "Los plazos de entrega son meramente estimativos y no comprometen responsabilidad alguna por demoras originadas en causas de fuerza mayor, caso fortuito o hechos de terceros ajenos.",
    "La bonificación eventualmente aplicada se calcula sobre el subtotal gravado y no resulta acumulable con otras promociones que pudieran encontrarse vigentes durante el período de facturación.",
    "El domicilio fiscal consignado tiene plena validez a los efectos de cualquier notificación judicial o extrajudicial que deba practicarse con motivo de la presente operación comercial.",
    "El incumplimiento de cualquiera de las condiciones aquí establecidas facultará al emisor a suspender la prestación de los servicios sin necesidad de interpelación previa alguna.",
    "Se deja constancia de que los valores unitarios detallados corresponden a la lista de precios vigente a la fecha de emisión y podrán ser actualizados en futuras operaciones.",
    "La aceptación de la mercadería sin observaciones importa la conformidad plena del receptor respecto de la cantidad, calidad y estado de los bienes entregados por el emisor.",
]


def _razon(rng: random.Random) -> str:
    a = rng.choice(["Global", "Andes", "Sur", "Delta", "Pampa", "Río", "Norte", "Alfa"])
    b = rng.choice(["Tech", "Servicios", "Comercial", "Industrias", "Group", "SA", "SRL"])
    return f"{a} {b}"


def generate_one(rng: random.Random, hard: bool = False, filler_reps: int = 1) -> tuple[str, dict]:
    # Cada monto elige su locale (mezcla es-AR/en-US en el MISMO doc → ambigüedad de formato).
    def amt(v: float) -> str:
        return _fmt_amount(v, rng.choice(["ar", "us"]))

    n_items = rng.randint(18, 36) if hard else rng.randint(3, 7)   # hard: tabla densa
    items, subtotal = [], 0.0
    for _ in range(n_items):
        cant = rng.randint(1, 40)
        punit = round(rng.uniform(500, 250000), 2)
        imp = round(cant * punit, 2)
        subtotal += imp
        items.append((cant, rng.choice(_CONCEPTOS), punit, imp))
    subtotal = round(subtotal, 2)
    rate = rng.choice([21.0, 10.5, 27.0])
    iva_amt = round(subtotal * rate / 100.0, 2)
    total = round(subtotal + iva_amt, 2)

    # distractores (NO forman parte de la suma; solo para confundir al extractor)
    descuento = round(rng.uniform(10000, 900000), 2)
    percepcion = round(rng.uniform(1000, 90000), 2)
    saldo_ant = round(rng.uniform(0, 2_000_000), 2)

    emisor_cuit = _cuit(rng)
    receptor_cuit = _cuit(rng)                 # distractor con la MISMA etiqueta "CUIT"
    comp = f"{rng.randint(1,9999):04d}-{rng.randint(0,99999999):08d}"
    orden = f"OC-{rng.randint(10000,999999)}"  # distractor de identificador

    # dos fechas (emisión = verdad; vencimiento = distractor). Emisión con día<=12 → dd/mm ambigua.
    y = rng.randint(2023, 2025)
    d_em, m_em = rng.randint(1, 12), rng.randint(1, 12)
    fecha_em = _fmt_date(y, m_em, d_em, rng.choice([0, 3]))     # dd/mm/yyyy o dd-mm-yyyy
    fecha_vto = _fmt_date(y, rng.randint(1, 12), rng.randint(13, 28), 0)
    letra = rng.choice(["A", "B", "C"])

    d_total, d_sub, d_iva = amt(total), amt(subtotal), amt(iva_amt)
    d_desc, d_perc, d_saldo = amt(descuento), amt(percepcion), amt(saldo_ant)
    d_rate = _fmt_amount(rate, "ar").rstrip("0").rstrip(",").rstrip(".")

    L = []
    L.append(f"Tipo: FACTURA {letra}")                 # el tipo separado del número (sin ambigüedad)
    L.append(f"Nro de comprobante: {comp}")
    L.append(f"Orden de compra: {orden}")
    L.append(f"Fecha de emisión: {fecha_em}")            # verdad (línea propia → ventana limpia)
    L.append(f"Fecha de vencimiento: {fecha_vto}")       # distractor (línea aparte)
    L.append(f"Razón Social: {_razon(rng)} S.A.   CUIT emisor: {emisor_cuit}")     # emisor (correcto)
    L.append(f"Cliente: {_razon(rng)}   CUIT del cliente: {receptor_cuit}")         # receptor (distractor)
    L.append("")
    L.append("Cant.  Concepto                        P.Unit         Importe")
    for cant, concepto, punit, imp in items:
        L.append(f"{cant:>4}   {concepto:<30} ARS {amt(punit):>14}   ARS {amt(imp):>16}")
    L.append("")
    L.append(f"Saldo anterior: ARS {d_saldo}")           # distractor de monto
    if rng.random() < 0.4:
        L.append("Subtotal gravado:")                    # valor en la línea siguiente
        L.append(f"ARS {d_sub}")
    else:
        L.append(f"Subtotal gravado: ARS {d_sub}")
    L.append(f"Descuento: ARS {d_desc}")                 # distractor de monto
    L.append(f"Alícuota IVA: {d_rate} %")                # tasa (campo limpio)
    L.append(f"Importe IVA: ARS {d_iva}")                # monto de IVA (campo limpio)
    L.append(f"Percepción IIBB: ARS {d_perc}")           # distractor de monto
    L.append(f"{rng.choice(_TOTAL_LABELS)}: ARS {d_total}")

    if hard:
        # Sepultar los valores de precisión en el MEDIO de texto largo (lost-in-the-middle):
        # boilerplate legal repetido filler_reps veces arriba y abajo. OCR limpio; la
        # dificultad es el VOLUMEN (a mayor filler_reps, más tokens → el LLM se ahoga).
        pre = ["CONDICIONES GENERALES DE CONTRATACIÓN"] + [rng.choice(_FILLER) for _ in range(filler_reps * 8)] + [""]
        tail = ["", "OBSERVACIONES Y CLÁUSULAS ADICIONALES"] + [rng.choice(_FILLER) for _ in range(filler_reps * 10)]
        L = pre + L + tail

    text = "\n".join(L) + "\n"

    truth = {
        "Total a pagar": d_total,
        "Subtotal": d_sub,
        "Importe IVA": d_iva,
        "Alícuota IVA": d_rate,
        "Fecha de emisión": fecha_em,
        "CUIT emisor": emisor_cuit,
        "Comprobante": comp,
    }
    return text, truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed", type=int, default=20260703)
    ap.add_argument("--out", default="data/invoices")
    ap.add_argument("--hard", action="store_true", help="docs densos y largos (lost-in-the-middle)")
    ap.add_argument("--filler-reps", type=int, default=1, help="multiplicador de boilerplate (más = más largo)")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    print(f"[generator] seed={args.seed} n={args.n} out={args.out}")
    for i in range(args.n):
        text, truth = generate_one(rng, hard=args.hard, filler_reps=args.filler_reps)
        stem = os.path.join(args.out, f"invoice_{i:05d}")
        with open(stem + ".txt", "w", encoding="utf-8") as f:
            f.write(text)
        with open(stem + ".truth.json", "w", encoding="utf-8") as f:
            json.dump(truth, f, ensure_ascii=False, indent=2)
    print(f"[generator] wrote {args.n} invoices (.txt + .truth.json)")


if __name__ == "__main__":
    main()
