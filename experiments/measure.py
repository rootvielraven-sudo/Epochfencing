# Autor: @Raven
"""Arnés de MEDICIÓN para el informe: barridos multi-semilla de ambos experimentos,
agregación (media ± desvío) y volcado a CSV/JSON.

Salidas en ``data/measure/``:
  exp1_raw.csv      una fila por (crash_fraction, modo, semilla)
  exp1_summary.csv  agregado media±desvío por (crash_fraction, modo)
  exp2_raw.csv      una fila por (corpus, condición VERA, semilla)
  exp2_summary.csv  agregado media±desvío por (corpus, condición)
  summary.json      todo junto, para las figuras y el informe

Correr:  env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv311/bin/python -m experiments.measure
"""
from __future__ import annotations

import csv
import json
import os
import statistics as st
from typing import Any

from experiments.exp1_node_crash import run as run_exp1
from experiments.exp2_content import run as run_exp2
from pipeline.corpus import generate_corpus

OUT = "data/measure"


def _agg(rows: list[dict], group_keys: list[str], metrics: list[str]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault(tuple(r[k] for k in group_keys), []).append(r)
    out = []
    for key, rs in groups.items():
        d = dict(zip(group_keys, key))
        d["n"] = len(rs)
        for m in metrics:
            vals = [float(r[m]) for r in rs]
            d[f"{m}_mean"] = round(st.mean(vals), 3)
            d[f"{m}_std"] = round(st.pstdev(vals), 3) if len(vals) > 1 else 0.0
        out.append(d)
    return out


def _write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def measure_exp1(n: int = 50, crash_fractions=(0.1, 0.2, 0.3, 0.4, 0.5),
                 seeds=range(1, 9)) -> tuple[list[dict], list[dict]]:
    raw = []
    for cf in crash_fractions:
        for s in seeds:
            res = run_exp1(n=n, crash_fraction=cf, crash_seed=s,
                           out_dir=os.path.join(OUT, "e1"))
            for mode, md in res["by_mode"].items():
                raw.append({
                    "crash_fraction": cf, "mode": mode, "seed": s,
                    "facturas_perdidas": md["facturas_perdidas"],
                    "facturas_duplicadas": md["facturas_duplicadas"],
                    "reclaims": md["reclaims"],
                    "zombie_writes_rejected": md["zombie_writes_rejected"],
                    "tiempo_recuperacion_medio": md["tiempo_recuperacion_medio"],
                    "pdf_igual": 1 if md["pdf_igual_a_sin_fallos"] else 0,
                })
    metrics = ["facturas_perdidas", "facturas_duplicadas", "reclaims",
               "zombie_writes_rejected", "tiempo_recuperacion_medio", "pdf_igual"]
    summary = _agg(raw, ["crash_fraction", "mode"], metrics)
    return raw, summary


def measure_exp2(seeds=range(1, 11)) -> tuple[list[dict], list[dict]]:
    # Corpus fijos (uno sintético, uno real); varía solo la asignación de fallos (semilla).
    corpora: dict[str, list] = {
        "sintetico": generate_corpus(100, seed=20261112, noise_tier="degraded"),
    }
    try:
        from pipeline.real_corpus import load_real_corpus
        real = load_real_corpus(noise_tier="degraded")
        if real:
            corpora["real"] = real
    except Exception as e:
        print(f"[measure] corpus real no disponible ({type(e).__name__}): se omite")

    raw = []
    for cname, inv in corpora.items():
        for s in seeds:
            res = run_exp2(seed=s, corruption_rate=0.25, hallucination_rate=0.25,
                           invoices=inv, out_dir=os.path.join(OUT, "e2"))
            for cond in ("con_vera", "sin_vera"):
                sc = res[cond]
                raw.append({
                    "corpus": cname, "condicion": cond, "seed": s,
                    "malas": sc["malas"], "buenas": sc["buenas"],
                    "tasa_deteccion": sc["tasa_deteccion"] or 0.0,
                    "falsos_positivos": sc["falsos_positivos"],
                    "falsos_negativos": sc["falsos_negativos"],
                    "det_corruption": sc["por_tipo"]["corruption"]["tasa"] or 0.0,
                    "det_hallucination": sc["por_tipo"]["hallucination"]["tasa"] or 0.0,
                })
    metrics = ["tasa_deteccion", "falsos_positivos", "falsos_negativos",
               "det_corruption", "det_hallucination"]
    summary = _agg(raw, ["corpus", "condicion"], metrics)
    return raw, summary


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    print("== midiendo Exp1 (caídas de nodo) ==")
    e1_raw, e1_sum = measure_exp1()
    _write_csv(os.path.join(OUT, "exp1_raw.csv"), e1_raw)
    _write_csv(os.path.join(OUT, "exp1_summary.csv"), e1_sum)
    print(f"   {len(e1_raw)} corridas → exp1_raw.csv / exp1_summary.csv")

    print("== midiendo Exp2 (corrupción + alucinación) ==")
    e2_raw, e2_sum = measure_exp2()
    _write_csv(os.path.join(OUT, "exp2_raw.csv"), e2_raw)
    _write_csv(os.path.join(OUT, "exp2_summary.csv"), e2_sum)
    print(f"   {len(e2_raw)} corridas → exp2_raw.csv / exp2_summary.csv")

    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"exp1": e1_sum, "exp2": e2_sum}, f, indent=2, ensure_ascii=False)
    print(f"   summary.json escrito en {OUT}/")


if __name__ == "__main__":
    main()
