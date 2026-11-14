# Autor: @Raven
"""Store por factura respaldado en REDIS, con epoch fencing ATÓMICO.

Igual contrato que ``store.EpochFencedStore`` (drop-in), pero el estado vive en Redis → la
recuperación es REAL entre procesos/nodos, no simulada en memoria. El fencing se hace con un
script Lua (compare-and-set sobre el epoch), atómico aunque varios workers commiteen a la vez.

Claves (``prefix`` por corrida, p.ej. ``tf:run123:``):
  {prefix}epoch:{id}   → epoch commiteado (int)         (para el CAS)
  {prefix}rec:{id}     → record serializado (JSON)
  {prefix}ids          → SET de invoice_ids commiteados
  {prefix}stale        → contador de sobre-escrituras zombi aceptadas (sin fencing)
"""
from __future__ import annotations

import json

from .model import Record
from .store import CommitOutcome

# KEYS: epoch_key, rec_key, ids_set, stale_counter   ARGV: invoice_id, epoch, payload, fence(0/1)
_CAS_LUA = """
local cur = tonumber(redis.call('GET', KEYS[1]))
if cur == nil then cur = -1 end
local newe = tonumber(ARGV[2])
if cur ~= -1 and newe == cur then
  return {'duplicate', cur}
end
if cur ~= -1 and newe < cur then
  if ARGV[4] == '1' then
    return {'stale_epoch', cur}
  else
    redis.call('INCR', KEYS[4])
  end
end
redis.call('SET', KEYS[1], newe)
redis.call('SET', KEYS[2], ARGV[3])
redis.call('SADD', KEYS[3], ARGV[1])
return {'ok', newe}
"""


class RedisEpochFencedStore:
    def __init__(self, client, root: str, *, fence_epochs: bool = True):
        self.c = client
        self.prefix = f"tf:{root.replace('/', '_')}:"
        self.fence_epochs = fence_epochs
        self._cas = self.c.register_script(_CAS_LUA)
        # corrida limpia: borrar claves previas de este prefijo
        self._clear()

    def _clear(self) -> None:
        keys = list(self.c.scan_iter(match=self.prefix + "*", count=500))
        if keys:
            self.c.delete(*keys)

    def _k(self, kind: str, iid: str = "") -> str:
        return f"{self.prefix}{kind}" + (f":{iid}" if iid else "")

    def commit(self, rec: Record) -> CommitOutcome:
        res = self._cas(
            keys=[self._k("epoch", rec.invoice_id), self._k("rec", rec.invoice_id),
                  self._k("ids"), self._k("stale")],
            args=[rec.invoice_id, rec.epoch,
                  json.dumps(rec.to_dict(), ensure_ascii=False),
                  "1" if self.fence_epochs else "0"],
        )
        reason = res[0].decode() if isinstance(res[0], bytes) else res[0]
        cur = int(res[1])
        return CommitOutcome(reason != "stale_epoch", reason, cur)

    def update_verdict(self, rec: Record) -> None:
        # No cambia epoch; reescribe el record (p.ej. tras adjuntar el veredicto VERA).
        self.c.set(self._k("rec", rec.invoice_id),
                   json.dumps(rec.to_dict(), ensure_ascii=False))
        self.c.sadd(self._k("ids"), rec.invoice_id)

    def get(self, invoice_id: str) -> Record | None:
        raw = self.c.get(self._k("rec", invoice_id))
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return Record.from_dict(json.loads(raw))

    def all_records(self) -> list[Record]:
        ids = [i.decode() if isinstance(i, bytes) else i
               for i in self.c.smembers(self._k("ids"))]
        out = []
        for iid in ids:
            r = self.get(iid)
            if r is not None:
                out.append(r)
        return out

    def committed_ids(self) -> set[str]:
        return {i.decode() if isinstance(i, bytes) else i
                for i in self.c.smembers(self._k("ids"))}

    @property
    def stale_overwrites(self) -> int:
        v = self.c.get(self._k("stale"))
        return int(v) if v else 0
