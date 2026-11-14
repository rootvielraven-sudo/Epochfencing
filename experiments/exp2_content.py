# Autor: @Raven
"""Experimento 2 — Corrupción silenciosa y alucinación (compuerta VERA).

Sobre facturas reconstruidas correctamente inyectamos errores controlados:
  - corrupción silenciosa → rompe un invariante de cálculo (dimensión CÁLCULO de VERA).
  - alucinación           → valor fabricado sin anclaje en el OCR (dimensión COBERTURA).

Como los errores los inyectamos nosotros, conocemos la verdad de referencia (``label``) sin
anotación manual. Comparamos CON VERA y SIN VERA, y medimos:
  - tasa de detección (facturas malas marcadas DEGRADED / facturas malas)
  - falsos positivos (facturas buenas marcadas DEGRADED)
  - falsos negativos (facturas malas que entran VERIFIED al PDF)

Sin caídas de nodo (este experimento aísla el contenido).
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from pipeline.corpus import generate_corpus
from pipeline.faults import ContentFaultPlan
from pipeline.gate import VeraGate
from pipeline.model import InvoiceState
from pipeline.orchestrator import LocalOrchestrator
from pipeline.reconstructor import DeterministicReconstructor


def _run(invoices, plan, vera_enabled, store_root) -> dict[str, str]:
    if os.path.isdir(store_root):
        shutil.rmtree(store_root)
    orch = LocalOrchestrator(
        invoices, mode="reclaim+epoch",
        gate=VeraGate(enabled=vera_enabled),
        reconstructor=DeterministicReconstructor(base_seed=1, fault_hook=plan.hook),
        crash_plan=None, store_root=store_root)
    orch.run()
    return {r.invoice_id: (r.state.value if r.state else "none")
            for r in orch.store.all_records()}


def _score(decision: dict[str, str], label: dict[str, str]) -> dict[str, Any]:
    bad = [i for i, l in label.items() if l != "clean"]
    good = [i for i, l in label.items() if l == "clean"]
    detected = sum(1 for i in bad if decision.get(i) == InvoiceState.DEGRADED.value)
    fp = sum(1 for i in good if decision.get(i) == InvoiceState.DEGRADED.value)
    fn = sum(1 for i in bad if decision.get(i) == InvoiceState.VERIFIED.value)
    # desglose por tipo de fallo
    by_type = {}
    for t in ("corruption", "hallucination"):
        ids_t = [i for i, l in label.items() if l == t]
        det_t = sum(1 for i in ids_t if decision.get(i) == InvoiceState.DEGRADED.value)
        by_type[t] = {"total": len(ids_t), "detectadas": det_t,
                      "tasa": round(det_t / len(ids_t), 3) if ids_t else None}
    return {
        "malas": len(bad), "buenas": len(good),
        "detectadas": detected,
        "tasa_deteccion": round(detected / len(bad), 3) if bad else None,
        "falsos_positivos": fp,
        "falsos_negativos": fn,
        "por_tipo": by_type,
    }


def run(n: int = 60, seed: int = 20261112, corruption_rate: float = 0.25,
        hallucination_rate: float = 0.25, out_dir: str = "data/exp2",
        invoices: list | None = None) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    if invoices is None:
        invoices = generate_corpus(n, seed=seed, noise_tier="degraded")
    ids = [i["invoice_id"] for i in invoices]
    # Facturas aptas para corrupción de cálculo (tienen líneas Σ=total).
    calc_ids = {i["invoice_id"] for i in invoices if i["truth"].get("items")}
    plan = ContentFaultPlan(ids, corruption_rate=corruption_rate,
                            hallucination_rate=hallucination_rate, seed=seed,
                            calc_ids=calc_ids or None)

    dec_vera = _run(invoices, plan, True, os.path.join(out_dir, "store_vera"))
    dec_none = _run(invoices, plan, False, os.path.join(out_dir, "store_novera"))
    return {
        "inyectado": plan.counts,
        "con_vera": _score(dec_vera, plan.label),
        "sin_vera": _score(dec_none, plan.label),
    }


def _print(res: dict[str, Any]) -> None:
    print("\n=== EXPERIMENTO 2 — CORRUPCIÓN Y ALUCINACIÓN ===\n")
    print("inyectado:", res["inyectado"], "\n")
    for key in ("con_vera", "sin_vera"):
        s = res[key]
        print(f"[{key}]  detección={s['tasa_deteccion']}  "
              f"FP={s['falsos_positivos']}  FN={s['falsos_negativos']}  "
              f"(malas={s['malas']}, buenas={s['buenas']})")
        for t, d in s["por_tipo"].items():
            print(f"     {t:<14} detectadas {d['detectadas']}/{d['total']}  tasa={d['tasa']}")
    print()


if __name__ == "__main__":
    _print(run())
