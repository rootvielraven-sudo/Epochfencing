# Autor: @Raven
"""Aplica ruido OCR a facturas LIMPIAS → dataset ruidoso por tier.

Por cada factura limpia escribe en el dir de salida:
  invoice_XXXX.txt         → NOISY (lo que lee el runner: el LLM Y VERA ven ESTE texto)
  invoice_XXXX.clean.txt   → limpio (referencia/auditoría)
  invoice_XXXX.truth.json  → valores LIMPIOS correctos (ground truth, NO cambia con el ruido)

El runner (run_eval.py) lee invoice_*.txt → el LLM extrae del texto ruidoso y VERA verifica
contra el MISMO texto ruidoso. Sin fuente limpia privilegiada para VERA.

Uso: python -m framework.vera.eval.make_noisy --clean-in data/invoices_20k --n 200 --tier med \
        --seed 20260703 --out data/invoices_ocr_med
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import shutil

from framework.vera.eval.ocr_noise import NOISE_TIERS, apply_noise


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean-in", default="data/invoices_20k")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--tier", required=True, choices=list(NOISE_TIERS))
    ap.add_argument("--seed", type=int, default=20260703)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    cleans = sorted(glob.glob(os.path.join(args.clean_in, "invoice_*.txt")))
    cleans = [p for p in cleans if not p.endswith(".clean.txt")][: args.n]
    print(f"[make_noisy] tier={args.tier} n={len(cleans)} seed={args.seed} "
          f"params={NOISE_TIERS[args.tier]}")

    for src in cleans:
        stem = os.path.basename(src)[:-4]                 # invoice_XXXXX
        clean_text = open(src, encoding="utf-8").read()
        noisy = apply_noise(clean_text, args.tier, rng)
        base = os.path.join(args.out, stem)
        with open(base + ".txt", "w", encoding="utf-8") as f:
            f.write(noisy)
        with open(base + ".clean.txt", "w", encoding="utf-8") as f:
            f.write(clean_text)
        shutil.copyfile(src.replace(".txt", ".truth.json"), base + ".truth.json")

    print(f"[make_noisy] escritas {len(cleans)} facturas ruidosas en {args.out}")


if __name__ == "__main__":
    main()
