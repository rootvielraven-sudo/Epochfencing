# Autor: @Raven
"""Experimento 4 — Abstención vs "reconstruir siempre" (sección 4).

Barre el umbral de confianza τ sobre las reconstrucciones de un modo productivo y mide, con la
verdad SOLO en el evaluador:
  - COBERTURA  = fracción de campos respondidos (no abstenidos).
  - EXACTITUD  = correctos / total de campos.
  - RIESGO     = equivocados / respondidos  (error entre lo que SÍ se afirma).

τ = 0 (+ require_evidence off) = política de "reconstruir siempre". Al subir τ, la cobertura
baja y el riesgo debería bajar: el sistema cambia respuestas inciertas por ``[UNRECOVERABLE]``.
"""
from __future__ import annotations

import json
import os
from typing import Any

from pipeline.abstention import AbstentionPolicy
from pipeline.corpus import generate_corpus
from pipeline.domain_context import DomainContext
from pipeline.reconstructor import UNRECOVERABLE, ConservativeReconstructor
from experiments.exp2_real import _SCORED, _norm


def _score_fields(ext, truth, ctx):
    tmap = {f.name: f.type for f in ctx.fields}
    answered = correct = wrong = 0
    for name in _SCORED:
        v = ext.get(name)
        if v is None or (isinstance(v, str) and v.strip() == UNRECOVERABLE):
            continue
        answered += 1
        if _norm(v, tmap.get(name, "text")) == _norm(truth.get(name), tmap.get(name, "text")):
            correct += 1
        else:
            wrong += 1
    return answered, correct, wrong


def run(n: int = 30, tier: str = "degraded", seed: int = 20261113,
        taus: tuple[float, ...] = (0.0, 0.3, 0.5, 0.7, 0.9),
        out_dir: str = "data/exp4", reconstructor=None, require_evidence_at_tau0: bool = False
        ) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    ctx = DomainContext.from_contract("factura")
    invs = generate_corpus(n, seed=seed, noise_tier=tier)
    rec = reconstructor or ConservativeReconstructor()

    # Reconstruir una sola vez; la política se aplica después (barata).
    results = [(inv, rec.reconstruct(inv["invoice_id"], 1, inv["ocr_text"], ctx)) for inv in invs]
    total_fields = n * len(_SCORED)

    rows = []
    for tau in taus:
        pol = AbstentionPolicy(min_confidence=tau,
                               require_evidence=(tau > 0 or require_evidence_at_tau0))
        ans = cor = wr = 0
        for inv, res in results:
            filtered = pol.apply(res, ctx.field_names())
            a, c, w = _score_fields(filtered.extraction, inv["truth"], ctx)
            ans += a; cor += c; wr += w
        rows.append({
            "tau": tau,
            "cobertura": round(ans / total_fields, 3),
            "exactitud": round(cor / total_fields, 3),
            "riesgo": round(wr / ans, 3) if ans else 0.0,          # error entre respondidos
        })
    out = {"n": n, "tier": tier, "modo": rec.mode, "total_campos": total_fields, "barrido": rows}
    with open(os.path.join(out_dir, f"exp4_{rec.mode}_{tier}.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _print(res: dict[str, Any]) -> None:
    print(f"\n=== EXPERIMENTO 4 — ABSTENCIÓN (modo={res['modo']}, OCR={res['tier']}, n={res['n']}) ===")
    print(f"{'tau':>6}{'cobertura':>12}{'exactitud':>12}{'riesgo':>10}")
    for r in res["barrido"]:
        print(f"{r['tau']:>6}{r['cobertura']:>12}{r['exactitud']:>12}{r['riesgo']:>10}")
    print("  (tau=0 = reconstruir siempre; al subir tau cae cobertura y debería caer riesgo)\n")


if __name__ == "__main__":
    _print(run())
