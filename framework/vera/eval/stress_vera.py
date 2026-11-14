# Autor: @Raven
"""STRESS-TEST del DETECTOR VERA (errores inyectados, sin LLM).

Mide a VERA como detector, desacoplado de la competencia del LLM:
  - por cada campo se le da a VERA el valor CORRECTO (=verdad) → debe dar SUPPORTED
    (si lo marca = falso positivo → mide especificidad),
  - y K mutaciones ALUCINADAS realistas (dígito, ×10 tipo dosis, migración, fecha swap,
    CUIT con checksum roto) → debe marcarlas (INVENTED/REVIEW) → mide capture (sensibilidad).

⚠️ Esto NO es el capture end-to-end sobre alucinaciones reales del LLM (ese necesita errores
del LLM). Es un unit-test del detector con positivos/negativos conocidos. Etiquetar aparte.

Determinista (seed logueado), sin red. Uso:
  python -m framework.vera.eval.stress_vera --in data/invoices_hard --n 100 --k 3 --out results/vera_stress
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import re
from collections import defaultdict
from decimal import Decimal

from framework.vera import Source, verify
from framework.vera.contract import load_contract_for
from framework.vera.eval import wilson_ci
from framework.vera.structured import _canonical_equal
from framework.vera.vera_schema import SemanticType
from framework.vera.vera_tribunal import parse_numeric_mentions
from framework.vera.vera_types import EntityVerdict

_FLAGGED = {EntityVerdict.INVENTED, EntityVerdict.REVIEW}


def _num(s: str):
    for m in parse_numeric_mentions(s.replace("%", " ")):
        if m.is_amount and m.magnitude is not None:
            return m.magnitude
    return None


def _mut_numeric(truth: str, rng: random.Random) -> str | None:
    v = _num(truth)
    if v is None:
        return None
    kind = rng.choice(["x10", "div10", "digit", "round", "off1"])
    if kind == "x10":
        nv = v * 10
    elif kind == "div10":
        nv = v / 10
    elif kind == "round":
        nv = (v / 1000).to_integral_value() * 1000
    elif kind == "off1":
        nv = v + Decimal(rng.choice([-1, 1]))
    else:  # cambiar un dígito de la parte entera
        s = str(int(v))
        i = rng.randrange(len(s))
        nd = str((int(s[i]) + rng.randint(1, 8)) % 10)
        nv = Decimal(s[:i] + nd + s[i + 1:] + "." + f"{v:.2f}".split(".")[1])
    return f"{nv:.2f}"


def _mut_date(truth: str, rng: random.Random) -> str | None:
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", truth)
    if not m:
        m2 = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", truth)
        if not m2:
            return None
        y, mo, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
    else:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
    kind = rng.choice(["swap", "year", "day"])
    if kind == "swap" and d <= 12 and mo <= 12 and d != mo:
        d, mo = mo, d
    elif kind == "year":
        y += rng.choice([-1, 1])
    else:
        d = (d % 28) + 1
    return f"{y:04d}-{mo:02d}-{d:02d}"


def _mut_cuit(truth: str, rng: random.Random) -> str | None:
    digs = re.sub(r"\D", "", truth)
    if len(digs) != 11:
        return None
    i = rng.randrange(11)
    nd = str((int(digs[i]) + rng.randint(1, 8)) % 10)
    m = digs[:i] + nd + digs[i + 1:]
    return f"{m[:2]}-{m[2:10]}-{m[10]}"


def _mut_ident(truth: str, rng: random.Random) -> str | None:
    s = list(truth)
    pos = [i for i, ch in enumerate(s) if ch.isdigit()]
    if not pos:
        return truth + "9"
    i = rng.choice(pos)
    s[i] = str((int(s[i]) + rng.randint(1, 8)) % 10)
    return "".join(s)


def _mut_pct(truth: str, rng: random.Random) -> str | None:
    opts = ["18", "19.5", "10.5", "21", "27", "33"]
    cur = _num(truth)
    cand = [o for o in opts if _num(o) != cur]
    return rng.choice(cand) if cand else None


def _mutate(spec, truth: str, rng: random.Random) -> str | None:
    t = spec.type
    if t in (SemanticType.NUMBER, SemanticType.CURRENCY_AMOUNT):
        return _mut_numeric(truth, rng)
    if t is SemanticType.PERCENTAGE:
        return _mut_pct(truth, rng)
    if t is SemanticType.DATE:
        return _mut_date(truth, rng)
    if t is SemanticType.IDENTIFIER:
        return _mut_cuit(truth, rng) if spec.fmt == "cuit" else _mut_ident(truth, rng)
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="indir", default="data/invoices_hard")
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--k", type=int, default=3, help="mutaciones por campo")
    ap.add_argument("--seed", type=int, default=20260703)
    ap.add_argument("--out", default="results/vera_stress")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    contract = load_contract_for("factura")
    txts = sorted(glob.glob(os.path.join(args.indir, "invoice_*.txt")))[: args.n]
    if not txts or contract is None:
        print("STOP: faltan facturas o contrato"); return
    print(f"[stress] seed={args.seed} n={len(txts)} k={args.k} (sin LLM, inyección)")

    # acumuladores por tipo: capture (TP/FN sobre inyectadas) + FP/TN (sobre correctas)
    cap = defaultdict(lambda: {"tp": 0, "fn": 0})
    spc = defaultdict(lambda: {"fp": 0, "tn": 0})

    for path in txts:
        text = open(path, encoding="utf-8").read()
        truth = json.load(open(path.replace(".txt", ".truth.json"), encoding="utf-8"))
        base = {s.key: truth.get(s.name) for s in contract}   # todo correcto

        # (1) correctos → esperar SUPPORTED (FP si se marca)
        rep = verify(base, Source.ocr(text), contract)
        for spec, r in zip(contract, rep.records):
            if r.verdict is EntityVerdict.SUPPORTED:
                spc[spec.type.value]["tn"] += 1
            elif r.verdict in _FLAGGED:
                spc[spec.type.value]["fp"] += 1
            # UNVERIFIED (valor no medible) no cuenta

        # (2) inyectar alucinaciones por campo → esperar flagged (capture)
        for spec in contract:
            tv = truth.get(spec.name)
            if tv is None:
                continue
            got = 0
            tries = 0
            while got < args.k and tries < args.k * 6:
                tries += 1
                mut = _mutate(spec, str(tv), rng)
                if mut is None or _canonical_equal(spec, str(mut), str(tv)):
                    continue   # mutación inválida o accidentalmente == verdad
                got += 1
                out = dict(base)
                out[spec.key] = mut
                r = verify(out, Source.ocr(text), contract)
                rec = next(x for x in r.records if x.entity_name == spec.name)
                if rec.verdict in _FLAGGED:
                    cap[spec.type.value]["tp"] += 1
                else:
                    cap[spec.type.value]["fn"] += 1

    os.makedirs(args.out, exist_ok=True)
    rows = []
    tot = {"tp": 0, "fn": 0, "fp": 0, "tn": 0}
    types = sorted(set(cap) | set(spc))
    for t in types:
        tp, fn = cap[t]["tp"], cap[t]["fn"]
        fp, tn = spc[t]["fp"], spc[t]["tn"]
        for k in tot:
            tot[k] += {"tp": tp, "fn": fn, "fp": fp, "tn": tn}[k]
        cl, ch = wilson_ci(tp, tp + fn)
        fl, fh = wilson_ci(fp, fp + tn)
        rows.append({"tipo": t, "inyectadas": tp + fn, "capturadas": tp,
                     "capture": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
                     "capture_ci_lo": round(cl, 4), "capture_ci_hi": round(ch, 4),
                     "correctas": fp + tn, "FP": fp,
                     "fp_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
                     "fp_ci_lo": round(fl, 4), "fp_ci_hi": round(fh, 4)})
    # overall
    tp, fn, fp, tn = tot["tp"], tot["fn"], tot["fp"], tot["tn"]
    cl, ch = wilson_ci(tp, tp + fn); fl, fh = wilson_ci(fp, fp + tn)
    overall = {"tipo": "OVERALL", "inyectadas": tp + fn, "capturadas": tp,
               "capture": round(tp / (tp + fn), 4) if (tp + fn) else 0.0,
               "capture_ci_lo": round(cl, 4), "capture_ci_hi": round(ch, 4),
               "correctas": fp + tn, "FP": fp,
               "fp_rate": round(fp / (fp + tn), 4) if (fp + tn) else 0.0,
               "fp_ci_lo": round(fl, 4), "fp_ci_hi": round(fh, 4)}
    rows.append(overall)

    path = os.path.join(args.out, "detector_stress.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

    print("\n" + "=" * 70)
    print("VERA DETECTOR STRESS-TEST (errores inyectados — NO es el capture del paper)")
    print("=" * 70)
    print(f"{'tipo':16} {'inyect':>7} {'capt':>5} {'capture':>8} {'Wilson95':>16} "
          f"{'correct':>8} {'FP':>4} {'fp_rate':>8}")
    for r in rows:
        print(f"{r['tipo']:16} {r['inyectadas']:>7} {r['capturadas']:>5} {r['capture']:>8} "
              f"{r['capture_ci_lo']:.3f}-{r['capture_ci_hi']:.3f}   "
              f"{r['correctas']:>8} {r['FP']:>4} {r['fp_rate']:>8}")
    print("\nCSV:", path)


if __name__ == "__main__":
    main()
