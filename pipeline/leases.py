# Autor: @Raven
"""Cola de trabajo con LEASES con vencimiento → recuperación ante caída de nodo.

Cada factura es un ítem. Un worker la *reclama* (``claim``) y recibe un lease con deadline.
Si el worker cae a mitad, no hace ``complete`` y el lease vence: otro worker la reclama y
la reejecuta. Se reprocesa ESA factura, no la corrida.

Cada reclamación incrementa el ``epoch`` del ítem → la reejecución lleva un epoch mayor,
que es lo que el store usa para rechazar al worker zombi anterior (ver ``store``).

El reloj es inyectable (``clock``) para que los experimentos avancen el tiempo de forma
determinista, sin ``sleep`` reales.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .model import Attempt, WorkState


@dataclass
class _Item:
    invoice_id: str
    state: WorkState = WorkState.PENDING
    epoch: int = 0                   # nº de intento (0 = aún no reclamada)
    worker_id: str | None = None
    lease_deadline: float = 0.0
    attempts: int = 0                # cuántas veces se reclamó (para límite de reintentos)


class LeaseQueue:
    """Cola in-memory con reclamación por vencimiento de lease.

    ``reclaim_enabled=False`` desactiva la recuperación (baseline "sin recuperación"):
    una factura reclamada que nunca se completa queda perdida.
    """

    def __init__(
        self,
        invoice_ids: list[str],
        *,
        lease_ttl: float = 2.0,
        max_attempts: int = 4,
        reclaim_enabled: bool = True,
        clock=time.monotonic,
    ):
        self.lease_ttl = lease_ttl
        self.max_attempts = max_attempts
        self.reclaim_enabled = reclaim_enabled
        self.clock = clock
        self._lock = threading.Lock()
        self._items: dict[str, _Item] = {i: _Item(i) for i in invoice_ids}
        self._reclaims = 0           # métrica: cuántas reclamaciones hubo

    # ── reclamación ───────────────────────────────────────────────────────────
    def claim(self, worker_id: str) -> Attempt | None:
        """Devuelve una factura para procesar, o ``None`` si no hay trabajo reclamable.

        Elegibles: PENDING, o LEASED con lease vencido (si ``reclaim_enabled``) que no haya
        agotado ``max_attempts``.
        """
        now = self.clock()
        with self._lock:
            for it in self._items.values():
                if it.state is WorkState.DONE:
                    continue
                reclaimable = (
                    it.state is WorkState.PENDING
                    or (it.state is WorkState.LEASED
                        and self.reclaim_enabled
                        and now >= it.lease_deadline)
                )
                if not reclaimable:
                    continue
                if it.attempts >= self.max_attempts:
                    continue
                if it.state is WorkState.LEASED:
                    self._reclaims += 1          # era un lease vencido que reclamamos
                it.state = WorkState.LEASED
                it.epoch += 1
                it.attempts += 1
                it.worker_id = worker_id
                it.lease_deadline = now + self.lease_ttl
                return Attempt(it.invoice_id, it.epoch, worker_id, it.lease_deadline)
            return None

    def complete(self, invoice_id: str, epoch: int) -> bool:
        """Marca DONE si el epoch coincide con el vigente (el worker sigue siendo el dueño).
        Un worker zombi (epoch viejo) no puede cerrar la factura."""
        with self._lock:
            it = self._items[invoice_id]
            if it.state is WorkState.DONE:
                return True
            if it.epoch != epoch:
                return False                     # llegó tarde: ya fue reclamada por otro
            it.state = WorkState.DONE
            return True

    def renew(self, invoice_id: str, epoch: int) -> bool:
        """Renueva el lease (heartbeat) si el worker sigue vigente."""
        now = self.clock()
        with self._lock:
            it = self._items[invoice_id]
            if it.state is WorkState.LEASED and it.epoch == epoch:
                it.lease_deadline = now + self.lease_ttl
                return True
            return False

    # ── estado / métricas ─────────────────────────────────────────────────────
    def pending_or_leased(self) -> int:
        with self._lock:
            return sum(1 for it in self._items.values() if it.state is not WorkState.DONE)

    def has_reclaimable(self) -> bool:
        """¿Queda trabajo que ALGÚN worker podría reclamar ahora o tras un vencimiento?
        False cuando todo está DONE o atascado por ``max_attempts``."""
        now = self.clock()
        with self._lock:
            for it in self._items.values():
                if it.state is WorkState.DONE:
                    continue
                if it.attempts >= self.max_attempts and it.state is WorkState.LEASED:
                    # agotó reintentos y su lease sigue vivo → no reclamable; pero si está
                    # PENDING con reintentos agotados tampoco. Lo tratamos como no reclamable.
                    continue
                if it.attempts >= self.max_attempts and it.state is WorkState.PENDING:
                    continue
                if it.state is WorkState.PENDING:
                    return True
                if (it.state is WorkState.LEASED and self.reclaim_enabled):
                    return True      # vencerá y será reclamable
            return False

    def exhausted_ids(self) -> list[str]:
        """Facturas que agotaron ``max_attempts`` sin completarse (→ DEGRADED por reintentos)."""
        with self._lock:
            return [it.invoice_id for it in self._items.values()
                    if it.state is not WorkState.DONE and it.attempts >= self.max_attempts]

    def lost_ids(self) -> list[str]:
        """Facturas no completadas (perdidas) al terminar la corrida."""
        with self._lock:
            return [it.invoice_id for it in self._items.values()
                    if it.state is not WorkState.DONE]

    @property
    def reclaims(self) -> int:
        return self._reclaims

    def snapshot(self) -> dict[str, dict]:
        with self._lock:
            return {i: {"state": it.state.value, "epoch": it.epoch, "attempts": it.attempts}
                    for i, it in self._items.items()}
