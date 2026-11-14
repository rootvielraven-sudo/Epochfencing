# Autor: @Raven
"""Inyecta FACTURAS REALES (de /home/athenas/Desktop/datos-demo) por la ruta de
verificación de cálculo (``verify_calc``, lo que el pipeline corre en el paso 4-ter).

Los NÚMEROS son reales, transcriptos de los PDFs (CompraGamer / Newton Station S.R.L.).
El paso del LLM se transcribe aquí (no se llama al modelo vivo), pero la verificación es
el código de producción. Cada factura trae su CalcContract (estructuras distintas:
Factura B simple, con impuesto interno, y multi-alícuota Ley 27.743).

Correr:  .venv/bin/python -m framework.vera.calc_eval.inject_real
"""
from __future__ import annotations

import copy

from ..calc_consistency import CalcState
from ..calc_contract import (
    CalcContract, CalcLineMap, CalcTaxMap, verify_calc,
)

# ── Factura B 00997224 (CompraGamer, 2 líneas, Σlíneas = total, sin IVA discriminado) ──
F1 = {
    "extraction": {
        "items": [
            {"cantidad": "1", "precio_unitario": "3.970,00", "importe": "3.970,00"},
            {"cantidad": "1", "precio_unitario": "55.830,00", "importe": "55.830,00"},
        ],
        "total": "$59.800,00",
    },
    "contract": CalcContract(
        lines=CalcLineMap(array="items", qty="cantidad", price="precio_unitario", amount="importe"),
        total="total"),
    "name": "Factura B 00997224 (2 líneas, Σ=total)",
}

# ── Factura B 01004205 (11 líneas + Impuesto Interno $29.657,10, total $1.003.852,63) ──
F2 = {
    "extraction": {
        "items": [
            {"cantidad": "1", "precio_unitario": "9.392,63", "importe": "9.392,63"},
            {"cantidad": "1", "precio_unitario": "2.950,00", "importe": "2.950,00"},
            {"cantidad": "1", "precio_unitario": "150.050,00", "importe": "150.050,00"},
            {"cantidad": "1", "precio_unitario": "226.900,00", "importe": "226.900,00"},
            {"cantidad": "1", "precio_unitario": "130.790,00", "importe": "130.790,00"},
            {"cantidad": "1", "precio_unitario": "89.550,00", "importe": "89.550,00"},
            {"cantidad": "1", "precio_unitario": "152.962,90", "importe": "152.962,90"},
            {"cantidad": "1", "precio_unitario": "30.300,00", "importe": "30.300,00"},
            {"cantidad": "1", "precio_unitario": "66.500,00", "importe": "66.500,00"},
            {"cantidad": "1", "precio_unitario": "55.950,00", "importe": "55.950,00"},
            {"cantidad": "1", "precio_unitario": "58.850,00", "importe": "58.850,00"},
        ],
        "imp_interno": "$29.657,10",
        "total": "$1.003.852,63",
    },
    "contract": CalcContract(
        lines=CalcLineMap(array="items", qty="cantidad", price="precio_unitario", amount="importe"),
        total="total",
        taxes=(CalcTaxMap(amount="imp_interno", tax_id="imp_interno"),)),  # solo monto (se suma)
    "name": "Factura B 01004205 (11 líneas + impuesto interno)",
}

# ── Factura B 01034901 (multi-alícuota Ley 27.743: IVA 10,5% + 21%) ──
F3 = {
    "extraction": {
        "items": [
            {"cantidad": "1", "precio_unitario": "5.175,00", "importe": "5.175,00"},
            {"cantidad": "1", "precio_unitario": "162.624,43", "importe": "162.624,43"},
            {"cantidad": "1", "precio_unitario": "223.348,42", "importe": "223.348,42"},
            {"cantidad": "1", "precio_unitario": "102.851,24", "importe": "102.851,24"},
            {"cantidad": "2", "precio_unitario": "15.067,87", "importe": "30.135,75"},  # +1c redondeo real
        ],
        "subtotal": "$524.134,84",
        "base_105": "$416.108,60", "alic_105": "10,5%", "iva_105": "$43.691,40",
        "base_21": "$108.026,24", "alic_21": "21%", "iva_21": "$22.685,51",
        "total": "$590.511,75",
    },
    "contract": CalcContract(
        lines=CalcLineMap(array="items", qty="cantidad", price="precio_unitario", amount="importe"),
        subtotal="subtotal", total="total",
        taxes=(
            CalcTaxMap(base="base_105", rate="alic_105", amount="iva_105", tax_id="iva105"),
            CalcTaxMap(base="base_21", rate="alic_21", amount="iva_21", tax_id="iva21"),
        )),
    "name": "Factura B 01034901 (multi-alícuota 10,5% + 21%, Ley 27.743)",
}

REAL = [F1, F2, F3]


def main() -> None:
    print("Inyección de FACTURAS REALES (datos-demo) por verify_calc "
          "(ruta del pipeline, paso 4-ter).\n")
    for f in REAL:
        r = verify_calc(f["extraction"], calc_contract=f["contract"])
        print(f"• {f['name']}")
        print(f"    tal cual (real):  {r.state.value.upper():<12} "
              f"invariantes={r.invariants_checked} incompletos={len(r.incomplete_reasons)}")

        # Inyección de UN error realista: adulterar el total.
        bad = copy.deepcopy(f["extraction"])
        cur = bad["total"]
        # suma $100.000 al total (cambio grande y evidente)
        from ..calc_contract import amount_to_cents, to_cents  # noqa
        bad_total_cents = amount_to_cents(cur) + to_cents(100000)
        pesos = int(bad_total_cents) // 100
        bad["total"] = f"$ {pesos:,}".replace(",", ".") + ",00"
        r2 = verify_calc(bad, calc_contract=f["contract"])
        viol = [v.rule.split(":")[0] for v in r2.violations]
        print(f"    total +$100.000:  {r2.state.value.upper():<12} viol={viol}\n")


if __name__ == "__main__":
    main()
