# Autor: @Raven
"""Modelo de datos del pipeline: factura, intento, resultado y estado final.

Todo es serializable a dict/JSON (las tareas Ray y el store atómico mueven dicts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class InvoiceState(StrEnum):
    """Estado FINAL de una factura de cara al PDF (lo decide la compuerta VERA)."""
    VERIFIED = "verified"    # pasó VERA → entra al PDF como verificada
    DEGRADED = "degraded"    # no pasó (o agotó reintentos) → entra con OCR crudo, marcada


class WorkState(StrEnum):
    """Estado de la factura en la COLA de trabajo (ortogonal a InvoiceState)."""
    PENDING = "pending"      # a la espera de un worker
    LEASED = "leased"        # reclamada por un worker, con lease vigente
    DONE = "done"            # commit aceptado por el store


@dataclass(frozen=True)
class Invoice:
    """Entrada: una factura degradada por OCR.

    ``ocr_text`` es lo ÚNICO que ven el reconstructor y VERA (no hay fuente limpia
    privilegiada). ``truth`` es la verdad de referencia (solo para medir), nunca se le
    pasa al worker.
    """
    invoice_id: str
    ocr_text: str
    truth: dict[str, Any]            # extracción correcta (ground truth, para métricas)

    def to_meta(self) -> dict[str, Any]:
        return {"invoice_id": self.invoice_id, "ocr_len": len(self.ocr_text)}


@dataclass
class Attempt:
    """Un intento de procesar una factura. El ``epoch`` crece en cada reintento y es la
    cerca (fence) contra escrituras zombi: el store solo acepta el epoch vigente."""
    invoice_id: str
    epoch: int
    worker_id: str
    lease_deadline: float            # t (monotónico) en que vence el lease


@dataclass
class Record:
    """Lo que un worker intenta COMMITEAR al store para una factura."""
    invoice_id: str
    epoch: int
    worker_id: str
    extraction: dict[str, Any]       # lo que "reconstruyó" el worker (puede venir corrupto)
    # Relleno por la compuerta VERA tras un commit aceptado:
    state: InvoiceState | None = None
    vera: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "invoice_id": self.invoice_id,
            "epoch": self.epoch,
            "worker_id": self.worker_id,
            "extraction": self.extraction,
            "state": self.state.value if self.state else None,
            "vera": self.vera,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Record":
        r = cls(
            invoice_id=d["invoice_id"], epoch=int(d["epoch"]),
            worker_id=d.get("worker_id", "?"), extraction=d.get("extraction", {}),
            vera=d.get("vera", {}),
        )
        if d.get("state"):
            r.state = InvoiceState(d["state"])
        return r
