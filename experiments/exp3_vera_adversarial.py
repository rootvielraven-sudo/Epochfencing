# Autor: @Raven
"""Experimento 3 — Capacidad REAL de VERA (sección 5).

Estresa a VERA con las 10 familias adversariales (``pipeline/adversarial.py``) + facturas
limpias, usando:
  - FUENTE INDEPENDIENTE: el OCR degradado (la misma entrada que vería el reconstructor), no
    el texto limpio privilegiado.
  - GATE ESTRICTO: REVIEW/UNVERIFIED en campo requerido NO pasa como VERIFIED.

Compara 4 configuraciones del verificador:
  sin · solo_cobertura · solo_calculo · cobertura+calculo

y reporta, por configuración: matriz de confusión (TP/TN/FP/FN), precision, recall (=tasa de
detección), F1, tasa de falsa aceptación (FAR) y de falso rechazo (FRR), con intervalo de
confianza de Wilson 95% para recall y FRR. Además, por FAMILIA de fallo: cuántas se detectan y
por qué ruta (cobertura/alucinación, cobertura/no-confirmable, cálculo).

Clase positiva = factura con error (``bad=True``): VERA DEBERÍA no-verificarla (degraded).
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

from pipeline.adversarial import FAMILIES, build_adversarial_cases
from pipeline.corpus import generate_corpus
from pipeline.gate import VeraGate

_CONFIGS = {
    "sin":                {"enabled": False, "check_coverage": False, "check_calc": False},
    "solo_cobertura":     {"enabled": True,  "check_coverage": True,  "check_calc": False},
    "solo_calculo":       {"enabled": True,  "check_coverage": False, "check_calc": True},
    "cobertura+calculo":  {"enabled": True,  "check_coverage": True,  "check_calc": True},
}


def _wilson(k: int, n: int) -> tuple[float, float]:
    """Intervalo de Wilson 95% para una proporción k/n."""
    if n == 0:
        return (0.0, 0.0)
    z = 1.96
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - half) / d), min(1.0, (c + half) / d))


def _src_text(case, source: str) -> str:
    return case.clean_text if source == "clean" else case.ocr_text


def _eval_config(cases, cfg, source) -> dict[str, Any]:
    gate = VeraGate(strict=True, **cfg)
    tp = tn = fp = fn = 0
    fam_detect: dict[str, dict[str, int]] = {f: {"total": 0, "det": 0} for f in FAMILIES}
    routes: dict[str, int] = {"hallucination": 0, "unverified": 0, "calc": 0}
    for c in cases:
        gr = None if not gate.enabled else gate.evaluate(c.extraction, _src_text(c, source))
        degraded = (gr is not None and not gr.passed)
        if c.bad:
            fam_detect[c.family]["total"] += 1
            if degraded:
                tp += 1
                fam_detect[c.family]["det"] += 1
                for r in gr.reasons:
                    routes[r] = routes.get(r, 0) + 1
            else:
                fn += 1
        else:
            fp += 1 if degraded else 0
            tn += 0 if degraded else 1
    prec = tp / (tp + fp) if (tp + fp) else None
    rec = tp / (tp + fn) if (tp + fn) else None
    f1 = (2 * prec * rec / (prec + rec)) if (prec and rec) else None
    return {
        "TP": tp, "TN": tn, "FP": fp, "FN": fn,
        "precision": _r(prec), "recall": _r(rec), "F1": _r(f1),
        "FAR": _r(fn / (tp + fn) if (tp + fn) else None),
        "FRR": _r(fp / (fp + tn) if (fp + tn) else None),
        "recall_ic95": [_r(x) for x in _wilson(tp, tp + fn)],
        "FRR_ic95": [_r(x) for x in _wilson(fp, fp + tn)],
        "rutas_deteccion": routes,
        "_familias": {f: {**d, "tasa": _r(d["det"] / d["total"]) if d["total"] else None}
                      for f, d in fam_detect.items()},
    }


def run(n: int = 120, seed: int = 20261113, clean_rate: float = 0.35,
        out_dir: str = "data/exp3", invoices: list | None = None) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    if invoices is None:
        invoices = generate_corpus(n, seed=seed, noise_tier="degraded")
    cases = build_adversarial_cases(invoices, seed=seed, clean_rate=clean_rate)
    out: dict[str, Any] = {
        "n_casos": len(cases), "malas": sum(c.bad for c in cases),
        "buenas": sum(not c.bad for c in cases), "por_fuente": {},
    }
    # fuente PRIVILEGIADA (clean_text) vs INDEPENDIENTE (OCR degradado).
    for source in ("clean", "ocr"):
        per_cfg = {name: _eval_config(cases, cfg, source) for name, cfg in _CONFIGS.items()}
        out["por_fuente"][source] = {
            "por_config": {k: {kk: vv for kk, vv in v.items() if kk != "_familias"}
                           for k, v in per_cfg.items()},
            "familias": per_cfg["cobertura+calculo"]["_familias"],
        }
    with open(os.path.join(out_dir, "exp3.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _r(x):
    return round(x, 3) if isinstance(x, float) else x


def _print(res: dict[str, Any]) -> None:
    print("\n=== EXPERIMENTO 3 — CAPACIDAD REAL DE VERA (gate estricto; 10 familias adversariales) ===")
    print(f"casos={res['n_casos']}  malas={res['malas']}  buenas={res['buenas']}")
    cols = ["TP", "TN", "FP", "FN", "precision", "recall", "F1", "FAR", "FRR"]
    etiqueta = {"clean": "FUENTE PRIVILEGIADA (clean_text) — ojo: fuga/circular",
                "ocr": "FUENTE INDEPENDIENTE (OCR degradado) — honesta"}
    for source, blk in res["por_fuente"].items():
        print(f"\n--- {etiqueta[source]} ---")
        hdr = f"{'config':<20}" + "".join(f"{c:>11}" for c in cols)
        print(hdr); print("-" * len(hdr))
        for name, d in blk["por_config"].items():
            print(f"{name:<20}" + "".join(f"{str(d[c]):>11}" for c in cols))
        cc = blk["por_config"]["cobertura+calculo"]
        print(f"  recall IC95={cc['recall_ic95']}  FRR IC95={cc['FRR_ic95']}  rutas={cc['rutas_deteccion']}")
    print("\nDetección por familia (cobertura+cálculo, fuente independiente):")
    for fam, d in res["por_fuente"]["ocr"]["familias"].items():
        print(f"   {fam:<34} {d['det']}/{d['total']}  tasa={d['tasa']}")
    print()


if __name__ == "__main__":
    _print(run())
