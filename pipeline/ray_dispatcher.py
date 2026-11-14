# Autor: @Raven
"""Ejecutor sobre Ray — plano de cómputo real (patrón *Pure Dispatcher*, Fig. 2 del paper).

El Dispatcher (driver):
  1. Reparte las facturas (unidades atómicas) a workers Ray independientes → reconstrucción
     EN PARALELO. El Dispatcher no reconstruye.
  2. Monitorea: una tarea que "cae" (``InjectedCrash``) se detecta como fallo.
  3. Recupera: la factura caída se RE-DESPACHA con un epoch mayor (reclamación por lease).
  4. Agrega: commitea al store con epoch fencing y pasa cada factura por la compuerta VERA.

Determinismo: la reconstrucción es ``temperatura 0`` con semilla por (invoice_id, epoch), así
que la reejecución tras una caída es repetible aunque el scheduling de Ray no lo sea.
"""
from __future__ import annotations

from typing import Any

from .faults import CrashPlan, InjectedCrash
from .gate import VeraGate
from .model import InvoiceState, Record
from .orchestrator import MODES, RunMetrics
from .reconstructor import DeterministicReconstructor


def _reconstruct_remote(invoice_id: str, epoch: int, attempt: int, ocr_text: str,
                        truth: dict, base_seed: int, crash: bool) -> dict[str, Any]:
    """Cuerpo del worker Ray: reconstruye una factura. Si ``crash`` → muere a mitad."""
    if crash:
        raise InjectedCrash(f"{invoice_id}@epoch{epoch}")
    rec = DeterministicReconstructor(base_seed=base_seed)
    extraction = rec.reconstruct(invoice_id, epoch, ocr_text, truth)
    return {"invoice_id": invoice_id, "epoch": epoch, "extraction": extraction}


class RayOrchestrator:
    """Corre el pipeline sobre Ray con reconstrucción paralela y recuperación por reclamación."""

    def __init__(
        self,
        invoices: list[dict[str, Any]],
        *,
        mode: str = "reclaim+epoch",
        gate: VeraGate | None = None,
        crash_plan: CrashPlan | None = None,
        store_root: str,
        base_seed: int = 0,
        max_attempts: int = 4,
        num_workers: int | None = None,
        redis_client=None,                # store con epoch fencing en Redis (distribuido real)
    ):
        assert mode in MODES
        self.invoices = {inv["invoice_id"]: inv for inv in invoices}
        self.ids = [inv["invoice_id"] for inv in invoices]
        self.mode = mode
        self.cfg = MODES[mode]
        self.gate = gate or VeraGate(enabled=True)
        self.crash_plan = crash_plan
        self.store_root = store_root
        self.base_seed = base_seed
        self.max_attempts = max_attempts
        self.num_workers = num_workers
        self.redis_client = redis_client

    def run(self) -> RunMetrics:
        import ray

        if not ray.is_initialized():
            ray.init(num_cpus=self.num_workers, ignore_reinit_error=True,
                     logging_level="ERROR", include_dashboard=False)

        remote = ray.remote(max_retries=0)(_reconstruct_remote)
        if self.redis_client is not None:
            from .redis_store import RedisEpochFencedStore
            store = RedisEpochFencedStore(self.redis_client, self.store_root,
                                          fence_epochs=self.cfg["fence"])
        else:
            from .store import EpochFencedStore
            store = EpochFencedStore(self.store_root, fence_epochs=self.cfg["fence"])
        m = RunMetrics(mode=self.mode, n_invoices=len(self.ids))
        if self.crash_plan:
            m.crashes_planned = self.crash_plan.planned

        epoch_of: dict[str, int] = {i: 0 for i in self.ids}
        attempts: dict[str, int] = {i: 0 for i in self.ids}
        done: set[str] = set()
        recovered_once: set[str] = set()

        # Ronda inicial: despachar TODAS las facturas en paralelo (Pure Dispatcher).
        pending = self._dispatch(remote, self.ids, epoch_of, attempts, m)

        while pending:
            ready, _ = ray.wait(list(pending.keys()), num_returns=1)
            for ref in ready:
                iid = pending.pop(ref)
                try:
                    out = ray.get(ref)
                except (InjectedCrash, ray.exceptions.RayTaskError):
                    # Caída detectada. Reclamar si el modo lo permite.
                    if self.cfg["reclaim"] and attempts[iid] < self.max_attempts:
                        m.reclaims += 1
                        recovered_once.add(iid)
                        new = self._dispatch(remote, [iid], epoch_of, attempts, m)
                        pending.update(new)
                    # modo 'none': no se reclama → factura perdida
                    continue
                # Éxito: commit fenced + compuerta VERA.
                rec = Record(iid, out["epoch"], "ray", out["extraction"])
                if store.commit(rec).accepted:
                    done.add(iid)
                    inv = self.invoices[iid]
                    gr = self.gate.evaluate(out["extraction"],
                                            inv.get("clean_text", inv["ocr_text"]))
                    rec.state = InvoiceState.VERIFIED if gr.passed else InvoiceState.DEGRADED
                    rec.vera = {"reasons": gr.reasons, "invented": gr.invented,
                                "calc": gr.calc_violations}
                    store.update_verdict(rec)
                    if iid in recovered_once:
                        m.recovered += 1

        # Métricas finales
        m.facturas_perdidas = len([i for i in self.ids if i not in done])
        m.facturas_duplicadas = store.stale_overwrites
        recs = store.all_records()
        m.verified = sum(1 for r in recs if r.state is InvoiceState.VERIFIED)
        m.degraded = sum(1 for r in recs if r.state is InvoiceState.DEGRADED)
        m.final_ids = sorted(r.invoice_id for r in recs)
        return m

    def _dispatch(self, remote, ids, epoch_of, attempts, m):
        refs = {}
        for iid in ids:
            epoch_of[iid] += 1
            attempts[iid] += 1
            crash = bool(self.crash_plan and
                         self.crash_plan.should_crash(iid, attempts[iid]))
            if crash:
                self.crash_plan.crashes_fired += 1
                m.crashes_fired += 1
            inv = self.invoices[iid]
            ref = remote.remote(iid, epoch_of[iid], attempts[iid], inv["ocr_text"],
                                inv["truth"], self.base_seed, crash)
            refs[ref] = iid
        return refs
