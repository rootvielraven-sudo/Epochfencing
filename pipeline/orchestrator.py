# Autor: @Raven
"""Orquestación del pipeline (patrón *Pure Dispatcher* del paper LLM-GSR): el coordinador
reparte, monitorea y agrega; NO reconstruye. Cada factura es la unidad atómica.

Dos ejecutores con la MISMA lógica de recuperación:

  - ``LocalOrchestrator``: un proceso, reloj VIRTUAL determinista. Es el motor de métricas
    de los experimentos (caídas y zombis reproducibles, sin matar procesos ni ``sleep``).
  - ``RayOrchestrator``: plano de cómputo real sobre Ray; reconstrucción en paralelo con
    workers independientes. Demuestra que corre distribuido.

Modos de recuperación (Exp. 1), cada uno activa/desactiva UN mecanismo:
  - ``none``         : sin recuperación (reclaim off, fencing off) → lo caído se pierde.
  - ``reclaim``      : lease con reclamación (fencing off) → recupera, pero un zombi tardío
                       puede pisar el record bueno (duplicado).
  - ``reclaim+epoch``: reclamación + epoch fencing → recupera y rechaza al zombi.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .domain_context import DomainContext
from .faults import CrashPlan
from .gate import VeraGate
from .leases import LeaseQueue
from .model import InvoiceState, Record
from .reconstructor import DeterministicReconstructor, ReconstructionResult
from .store import EpochFencedStore

MODES = {
    "none":          {"reclaim": False, "fence": False},
    "reclaim":       {"reclaim": True,  "fence": False},
    "reclaim+epoch": {"reclaim": True,  "fence": True},
}


@dataclass
class RunMetrics:
    mode: str
    n_invoices: int
    crashes_planned: int = 0
    crashes_fired: int = 0
    reclaims: int = 0
    zombie_writes_attempted: int = 0
    zombie_writes_rejected: int = 0
    facturas_perdidas: int = 0
    facturas_duplicadas: int = 0          # zombis aceptados por falta de fencing
    recovery_ticks_total: int = 0
    recovered: int = 0
    verified: int = 0
    degraded: int = 0
    final_ids: list[str] = field(default_factory=list)

    @property
    def tiempo_recuperacion_medio(self) -> float:
        return self.recovery_ticks_total / self.recovered if self.recovered else 0.0

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in self.__dict__.items() if k != "final_ids"}
        d["tiempo_recuperacion_medio"] = round(self.tiempo_recuperacion_medio, 3)
        return d


class _VirtualClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


class LocalOrchestrator:
    """Ejecutor determinista con reloj virtual. Simula caídas de nodo y escrituras zombi."""

    def __init__(
        self,
        invoices: list[dict[str, Any]],
        *,
        mode: str = "reclaim+epoch",
        gate: VeraGate | None = None,
        reconstructor: DeterministicReconstructor | None = None,
        crash_plan: CrashPlan | None = None,
        store_root: str,
        lease_ttl: float = 2.0,
        max_attempts: int = 4,
        zombie_fraction: float = 1.0,     # de las caídas, cuántas "resucitan" y escriben tarde
        redis_client=None,                # si se pasa → store con epoch fencing EN REDIS
        doc_type: str = "factura", client: str = "default", config_root: str = "config",
        vera_source: str = "clean",       # "clean" (histórico) | "ocr" (OCR degradado, INDEPENDIENTE)
    ):
        assert mode in MODES, f"modo inválido: {mode}"
        assert vera_source in ("clean", "ocr")
        self.invoices = {inv["invoice_id"]: inv for inv in invoices}
        self.ids = [inv["invoice_id"] for inv in invoices]
        self.mode = mode
        self.cfg = MODES[mode]
        self.gate = gate or VeraGate(enabled=True)
        self.reconstructor = reconstructor or DeterministicReconstructor()
        self.doc_type = doc_type
        self.client = client
        self.config_root = config_root
        self.vera_source = vera_source
        self._ctx: DomainContext | None = None
        # Evidencia/incertidumbre por factura de los modos productivos (para Exp. 2-real).
        self.reconstruction_results: dict[str, ReconstructionResult] = {}
        self.crash_plan = crash_plan
        self.lease_ttl = lease_ttl
        self.max_attempts = max_attempts
        self.zombie_fraction = zombie_fraction

        self.clock = _VirtualClock()
        if redis_client is not None:
            from .redis_store import RedisEpochFencedStore
            self.store = RedisEpochFencedStore(redis_client, store_root,
                                               fence_epochs=self.cfg["fence"])
        else:
            self.store = EpochFencedStore(store_root, fence_epochs=self.cfg["fence"])
        self.queue = LeaseQueue(
            self.ids, lease_ttl=lease_ttl, max_attempts=max_attempts,
            reclaim_enabled=self.cfg["reclaim"], clock=self.clock,
        )

    def run(self) -> RunMetrics:
        m = RunMetrics(mode=self.mode, n_invoices=len(self.ids))
        if self.crash_plan:
            m.crashes_planned = self.crash_plan.planned

        # Un worker "muerto" que puede resucitar: (invoice_id, epoch viejo, tick de caída, record).
        pending_zombies: list[tuple[str, int, float, Record]] = []
        crash_tick: dict[str, float] = {}      # primer tick de caída por factura (para t. recup.)

        worker_seq = 0
        guard = 0
        max_guard = (len(self.ids) + 1) * (self.max_attempts + 2) * 4

        while self.queue.pending_or_leased() > 0 and guard < max_guard:
            guard += 1
            worker_seq += 1
            worker_id = f"w{worker_seq}"
            attempt = self.queue.claim(worker_id)

            if attempt is None:
                # No hay nada reclamable AHORA. Si hay leases vivos, avanzar el reloj para
                # que venzan (simula el timeout que dispara la reclamación).
                if self.queue.pending_or_leased() > 0 and self.cfg["reclaim"]:
                    self.clock.advance(self.lease_ttl + 0.01)
                    # Antes de reclamar, los zombis cuyo lease venció pueden escribir tarde.
                    self._flush_zombies(pending_zombies, m)
                    continue
                break  # modo 'none': lo que quedó sin completar está perdido

            inv = self.invoices[attempt.invoice_id]
            n_attempt = self._attempt_number(attempt.invoice_id)

            # ¿Este intento cae a mitad? (caída de nodo)
            if self.crash_plan and self.crash_plan.should_crash(attempt.invoice_id, n_attempt):
                self.crash_plan.crashes_fired += 1
                m.crashes_fired += 1
                crash_tick.setdefault(attempt.invoice_id, self.clock.t)
                # El worker alcanzó a reconstruir pero murió antes de commitear. Si es un
                # zombi, resucitará y hará un commit tardío con su epoch (viejo).
                rec = Record(attempt.invoice_id, attempt.epoch, worker_id,
                             self._reconstruct(attempt.invoice_id, attempt.epoch, inv))
                if self._is_zombie(attempt.invoice_id):
                    pending_zombies.append((attempt.invoice_id, attempt.epoch, self.clock.t, rec))
                # No hace complete() → su lease vencerá y (si reclaim) se reejecuta.
                continue

            # Intento exitoso: reconstruye, commitea (fenced), cierra el lease, aplica VERA.
            extraction = self._reconstruct(attempt.invoice_id, attempt.epoch, inv)
            rec = Record(attempt.invoice_id, attempt.epoch, worker_id, extraction)
            outcome = self.store.commit(rec)
            if outcome.accepted:
                self.queue.complete(attempt.invoice_id, attempt.epoch)
                # Fuente de verificación: "clean" (histórico, privilegiada) o "ocr" (OCR
                # degradado = la MISMA entrada que vio el reconstructor → INDEPENDIENTE).
                src = inv["ocr_text"] if self.vera_source == "ocr" \
                    else inv.get("clean_text", inv["ocr_text"])
                gr = self.gate.evaluate(extraction, src)
                rec.state = InvoiceState.VERIFIED if gr.passed else InvoiceState.DEGRADED
                rec.vera = {"reasons": gr.reasons, "invented": gr.invented,
                            "calc": gr.calc_violations}
                self.store.update_verdict(rec)
                if attempt.invoice_id in crash_tick:
                    m.recovered += 1
                    m.recovery_ticks_total += int(self.clock.t - crash_tick[attempt.invoice_id])

        # Fin de corrida: los zombis que queden intentan su escritura tardía.
        self._flush_zombies(pending_zombies, m, force=True)

        # Facturas que agotaron reintentos sin completarse → DEGRADED por reintentos.
        for iid in self.queue.exhausted_ids():
            if self.store.get(iid) is None:
                inv = self.invoices[iid]
                rec = Record(iid, -1, "exhausted", inv["truth"])
                rec.state = InvoiceState.DEGRADED
                rec.vera = {"reasons": ["retries_exhausted"]}
                self.store.update_verdict(rec)

        self._finalize_metrics(m)
        return m

    # ── helpers ───────────────────────────────────────────────────────────────
    def _context(self) -> DomainContext:
        if self._ctx is None:
            self._ctx = DomainContext.from_contract(
                self.doc_type, client=self.client, config_root=self.config_root)
        return self._ctx

    def _reconstruct(self, invoice_id: str, epoch: int, inv: dict) -> dict:
        """Conduce el reconstructor. Oracle (baseline) recibe la verdad por llamada; los modos
        productivos (B/C) reciben SOLO ocr_text + contexto de dominio (sin verdad)."""
        r = self.reconstructor
        if getattr(r, "productive", False):
            res = r.reconstruct(invoice_id, epoch, inv["ocr_text"], self._context())
            self.reconstruction_results[invoice_id] = res
            return res.extraction
        return r.reconstruct(invoice_id, epoch, inv["ocr_text"], inv["truth"])

    def _attempt_number(self, invoice_id: str) -> int:
        return self.queue.snapshot()[invoice_id]["attempts"]

    def _is_zombie(self, invoice_id: str) -> bool:
        # Determinista: una fracción de las caídas resucita como zombi.
        if self.zombie_fraction >= 1.0:
            return True
        return (hash(("zombie", invoice_id)) & 0xFFFF) / 0xFFFF < self.zombie_fraction

    def _flush_zombies(self, pending: list, m: RunMetrics, force: bool = False) -> None:
        """Los workers 'muertos' resucitan tras vencer su lease e intentan commitear tarde."""
        still: list = []
        for (iid, epoch, t_crash, rec) in pending:
            awake = force or (self.clock.t > t_crash)
            # Solo es zombi conflictivo si la factura ya fue reclamada/avanzó (epoch > el suyo).
            cur = self.store.get(iid)
            reclaimed = self.queue.snapshot()[iid]["epoch"] > epoch
            if awake and reclaimed:
                m.zombie_writes_attempted += 1
                outcome = self.store.commit(rec)   # epoch viejo
                if not outcome.accepted:
                    m.zombie_writes_rejected += 1
                # si accepted con epoch<cur → store cuenta stale_overwrite (duplicado)
            else:
                still.append((iid, epoch, t_crash, rec))
        pending[:] = still

    def _finalize_metrics(self, m: RunMetrics) -> None:
        m.reclaims = self.queue.reclaims
        m.facturas_perdidas = len(self.queue.lost_ids())
        m.facturas_duplicadas = self.store.stale_overwrites
        recs = self.store.all_records()
        m.verified = sum(1 for r in recs if r.state is InvoiceState.VERIFIED)
        m.degraded = sum(1 for r in recs if r.state is InvoiceState.DEGRADED)
        m.final_ids = sorted(r.invoice_id for r in recs)
