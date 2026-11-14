# Autor: @Raven
"""Almacenamiento por factura con ESCRITURA ATÓMICA y EPOCH FENCING.

Cubre el fallo de *ejecución zombi*: a un worker se lo dio por muerto (su lease venció y
la factura se reclamó con un epoch mayor), pero sigue vivo y escribe tarde. El store
rechaza cualquier commit cuyo ``epoch`` sea menor al mayor ya visto para esa factura.

- Atómico: se escribe a un archivo temporal y se hace ``os.replace`` (rename atómico en
  POSIX) → nunca queda un JSON a medias, ni siquiera si el proceso muere escribiendo.
- Idempotente: commitear dos veces el MISMO epoch es un no-op aceptado (no duplica).

El estado vive en disco (un JSON por factura) para que sea observable entre procesos; una
caché en memoria acelera las lecturas (``get``), PERO el fencing NO se fía de ella: valida el
epoch contra el estado DURABLE (disco) dentro de un lock de fichero (``flock``) que serializa a
todos los procesos → comprobación-del-token + escritura en una sola operación atómica.

GARANTÍA (denominación técnica, sección 6):
  - Ejecución: **at-least-once** — un intento caído se reclama y se RE-EJECUTA (la factura
    puede procesarse más de una vez).
  - Efecto en el store: **exactly-once effect** vía commits IDEMPOTENTES y con EPOCH FENCING —
    queda exactamente un record por factura (el de mayor epoch aceptado); los commits zombi
    (epoch < vigente) se rechazan y el re-commit del mismo epoch es no-op. El estado durable
    final es el de un procesamiento único.
  NO es exactly-once *processing* (el trabajo de reconstrucción puede repetirse).
"""
from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass

try:
    import fcntl   # POSIX: lock de fichero ENTRE PROCESOS (flock)
except ImportError:  # pragma: no cover - no-POSIX
    fcntl = None

from .model import Record


@dataclass
class CommitOutcome:
    accepted: bool
    reason: str                      # "ok" | "stale_epoch" | "duplicate"
    current_epoch: int               # epoch vigente tras el intento


class EpochFencedStore:
    """Un JSON por factura en ``root``; acepta commits en orden de epoch no decreciente."""

    def __init__(self, root: str, *, fence_epochs: bool = True):
        self.root = root
        self.fence_epochs = fence_epochs          # si False → NO rechaza zombis (baseline)
        os.makedirs(root, exist_ok=True)
        self._lock = threading.Lock()
        self._cache: dict[str, Record] = {}
        self._stale_overwrites = 0       # zombis aceptados por falta de fencing (duplicados)
        self._load_existing()

    # ── persistencia ────────────────────────────────────────────────────────
    def _path(self, invoice_id: str) -> str:
        return os.path.join(self.root, f"{invoice_id}.json")

    def _load_existing(self) -> None:
        for fn in os.listdir(self.root):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(self.root, fn), encoding="utf-8") as f:
                        rec = Record.from_dict(json.load(f))
                    self._cache[rec.invoice_id] = rec
                except Exception:
                    pass

    def _write_atomic(self, rec: Record) -> None:
        path = self._path(rec.invoice_id)
        tmp = f"{path}.{os.getpid()}.{rec.epoch}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec.to_dict(), f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)                     # rename atómico

    def _read_durable(self, invoice_id: str) -> Record | None:
        """Lee el epoch VIGENTE del DISCO (estado autoritativo), no de la caché. Es lo que hace
        el fencing correcto entre procesos: la caché de otra instancia puede estar obsoleta."""
        path = self._path(invoice_id)
        try:
            with open(path, encoding="utf-8") as f:
                return Record.from_dict(json.load(f))
        except FileNotFoundError:
            return None
        except Exception:
            return None

    class _FileLock:
        """Lock de fichero ENTRE PROCESOS (flock). No-op si no hay fcntl."""
        def __init__(self, path: str):
            self.path = path
            self._fh = None

        def __enter__(self):
            if fcntl is None:
                return self
            self._fh = open(self.path, "w")
            fcntl.flock(self._fh, fcntl.LOCK_EX)
            return self

        def __exit__(self, *exc):
            if self._fh is not None:
                fcntl.flock(self._fh, fcntl.LOCK_UN)
                self._fh.close()
                self._fh = None

    def _lockpath(self, invoice_id: str) -> str:
        return os.path.join(self.root, f".{invoice_id}.lock")

    # ── API ──────────────────────────────────────────────────────────────────
    def commit(self, rec: Record) -> CommitOutcome:
        """Intenta persistir ``rec``. Rechaza si su epoch quedó obsoleto (zombi).

        El fencing valida el token contra el estado DURABLE (disco), dentro de un lock de
        fichero que serializa a todos los procesos sobre el mismo store → la comprobación de
        epoch y la escritura son una sola operación atómica (sin TOCTOU entre procesos)."""
        with self._lock, self._FileLock(self._lockpath(rec.invoice_id)):
            cur = self._read_durable(rec.invoice_id)   # ← DISCO, no caché
            cur_epoch = cur.epoch if cur else -1
            if cur is not None and rec.epoch == cur_epoch:
                self._cache[rec.invoice_id] = rec      # idempotente: no duplica
                return CommitOutcome(True, "duplicate", cur_epoch)
            if cur is not None and rec.epoch < cur_epoch:
                if self.fence_epochs:
                    self._cache[rec.invoice_id] = cur  # refresca caché con lo durable
                    return CommitOutcome(False, "stale_epoch", cur_epoch)
                # sin fencing: el zombi PISA el record bueno → corrupción/duplicado
                self._stale_overwrites += 1
            self._write_atomic(rec)
            self._cache[rec.invoice_id] = rec
            return CommitOutcome(True, "ok", rec.epoch)

    def update_verdict(self, rec: Record) -> None:
        """Reescribe el record (p.ej. tras adjuntarle el veredicto VERA). No cambia epoch.
        Bajo el mismo lock de fichero para no correr contra un commit concurrente."""
        with self._lock, self._FileLock(self._lockpath(rec.invoice_id)):
            self._cache[rec.invoice_id] = rec
            self._write_atomic(rec)

    def get(self, invoice_id: str) -> Record | None:
        with self._lock:
            return self._cache.get(invoice_id)

    def all_records(self) -> list[Record]:
        with self._lock:
            return list(self._cache.values())

    def committed_ids(self) -> set[str]:
        with self._lock:
            return set(self._cache.keys())

    @property
    def stale_overwrites(self) -> int:
        """Cuántos commits obsoletos (zombi) se aceptaron por falta de epoch fencing."""
        return self._stale_overwrites
