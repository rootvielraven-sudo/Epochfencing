# Autor: @Raven
"""Experimento 1 — Caídas de nodo.

Matamos workers durante la corrida y comparamos tres modos de recuperación:
  none · reclaim · reclaim+epoch

Sobre el MISMO corpus y el MISMO plan de caídas, medimos por modo:
  - facturas perdidas
  - facturas duplicadas (zombis aceptados por falta de epoch fencing)
  - tiempo de recuperación medio (ticks del reloj virtual)
  - si el resultado final coincide con una corrida SIN fallos (PDF idéntico)

La compuerta VERA se deja ACTIVA pero sin inyección de contenido → una corrida sin fallos
debería dar todo VERIFICADO; cualquier divergencia aísla el efecto de las caídas.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

from pipeline.corpus import generate_corpus
from pipeline.faults import CrashPlan
from pipeline.gate import VeraGate
from pipeline.orchestrator import MODES, LocalOrchestrator
from pipeline.reconstructor import DeterministicReconstructor


def _run(invoices, mode, crash_plan, store_root, gate) -> dict[str, Any]:
    if os.path.isdir(store_root):
        shutil.rmtree(store_root)
    orch = LocalOrchestrator(
        invoices, mode=mode, gate=gate,
        reconstructor=DeterministicReconstructor(base_seed=1),
        crash_plan=crash_plan, store_root=store_root, lease_ttl=2.0, max_attempts=5)
    m = orch.run()
    # firma del resultado: (id, extraction, state) por factura, para comparar "PDF idéntico"
    sig = {r.invoice_id: (repr(r.extraction), r.state.value if r.state else None)
           for r in orch.store.all_records()}
    return {"metrics": m, "sig": sig}


def run(n: int = 40, seed: int = 20261112, crash_fraction: float = 0.3,
        out_dir: str = "data/exp1", invoices: list | None = None,
        crash_seed: int = 7) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    if invoices is None:
        invoices = generate_corpus(n, seed=seed, noise_tier="degraded")
    ids = [i["invoice_id"] for i in invoices]
    gate = VeraGate(enabled=True)

    # Referencia: corrida SIN fallos (plan vacío) en el modo robusto.
    ref = _run(invoices, "reclaim+epoch", CrashPlan(ids, crash_fraction=0.0),
               os.path.join(out_dir, "store_ref"), gate)
    ref_sig = ref["sig"]

    results = {"reference_no_faults": ref["metrics"].to_dict(), "by_mode": {}}
    for mode in MODES:
        crash = CrashPlan(ids, crash_fraction=crash_fraction, crash_attempt=1, seed=crash_seed)
        r = _run(invoices, mode, crash, os.path.join(out_dir, f"store_{mode.replace('+','_')}"), gate)
        md = r["metrics"].to_dict()
        # ¿El resultado final coincide con la corrida sin fallos?
        same = r["sig"] == ref_sig
        missing = sorted(set(ref_sig) - set(r["sig"]))
        diverged = sorted(k for k in r["sig"] if k in ref_sig and r["sig"][k] != ref_sig[k])
        md["pdf_igual_a_sin_fallos"] = same
        md["faltan_en_pdf"] = len(missing)
        md["divergen_contenido"] = len(diverged)
        results["by_mode"][mode] = md
    return results


def _print(results: dict[str, Any]) -> None:
    print("\n=== EXPERIMENTO 1 — CAÍDAS DE NODO ===\n")
    cols = ["facturas_perdidas", "facturas_duplicadas", "reclaims",
            "zombie_writes_attempted", "zombie_writes_rejected",
            "tiempo_recuperacion_medio", "pdf_igual_a_sin_fallos"]
    hdr = f"{'modo':<16}" + "".join(f"{c:>26}" for c in cols)
    print(hdr); print("-" * len(hdr))
    for mode, md in results["by_mode"].items():
        row = f"{mode:<16}" + "".join(f"{str(md.get(c)):>26}" for c in cols)
        print(row)
    print()


if __name__ == "__main__":
    _print(run())
