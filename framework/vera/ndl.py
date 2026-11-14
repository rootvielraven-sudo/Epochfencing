# Autor: @Raven
"""VERA — NDL: capa diacrónica de determinismo numérico (anti-pattern cache).

Cuarta capa de VERA del paper: persiste cada divergencia/fabricación detectada como
un ANTI-PATRÓN de polaridad negativa, indexado por (doc_type, field) y por el CONTEXTO
en que ocurrió. En inferencias futuras, si el contexto coincide (Jaccard ≥ 0.75) con un
anti-patrón conocido, VERA aplica verificación reforzada (degrada un SUPPORTED del plano a
REVIEW) o escala a revisión humana. Monotónica: acumula señal en el tiempo.

Store INYECTABLE (get/setdefault dict-like): por defecto en memoria; un adapter Redis es un
envoltorio delgado. Determinista, sin LLM. Solo aplica a familias de precisión (números,
identificadores, fechas, períodos) — no a texto libre.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .vera_schema import SemanticType
from .vera_types import EntityVerdict

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")

_PRECISION_TYPES = {
    SemanticType.NUMBER, SemanticType.CURRENCY_AMOUNT, SemanticType.PERCENTAGE,
    SemanticType.DATE, SemanticType.PERIOD, SemanticType.IDENTIFIER,
}


def _toks(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower().translate(_ACCENTS)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter, union = len(a & b), len(a | b)
    return inter / union if union else 0.0


def _norm(v: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (v or "").lower().translate(_ACCENTS))


@dataclass
class AntiPattern:
    field_name: str
    context: frozenset[str]
    hallucinated: str
    corrected: str | None = None
    count: int = 1


@dataclass(frozen=True)
class NDLSignal:
    matched: bool                 # el contexto coincide con algún anti-patrón conocido
    value_hit: bool = False       # además, el valor coincide con una alucinación conocida
    count: int = 0
    reason: str = ""


class _DictStore:
    """Store en memoria por defecto. ``load`` devuelve la MISMA lista (mutación in-place ok)."""

    def __init__(self) -> None:
        self._d: dict[str, list[AntiPattern]] = {}

    def load(self, key: str) -> list[AntiPattern]:
        return self._d.setdefault(key, [])

    def save(self, key: str, entries: list[AntiPattern]) -> None:
        self._d[key] = entries


class RedisNDLStore:
    """Store persistente sobre un cliente Redis SÍNCRONO (redis.Redis). Serializa la lista de
    anti-patrones por clave a JSON. ``client`` solo necesita get(key)->bytes|str|None y set(key,str)."""

    def __init__(self, client: Any, prefix: str = "vera:ndl:") -> None:
        self.c = client
        self.prefix = prefix

    def load(self, key: str) -> list[AntiPattern]:
        import json
        raw = self.c.get(self.prefix + key)
        if not raw:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return [AntiPattern(field_name=d["field_name"], context=frozenset(d["context"]),
                            hallucinated=d["hallucinated"], corrected=d.get("corrected"),
                            count=d.get("count", 1)) for d in json.loads(raw)]

    def save(self, key: str, entries: list[AntiPattern]) -> None:
        import json
        payload = [{"field_name": e.field_name, "context": sorted(e.context),
                    "hallucinated": e.hallucinated, "corrected": e.corrected,
                    "count": e.count} for e in entries]
        self.c.set(self.prefix + key, json.dumps(payload, ensure_ascii=False))


class NDL:
    """Anti-pattern cache diacrónico. ``store`` implementa load(key)->list y save(key, list);
    por defecto en memoria (``_DictStore``). Para persistir: ``NDL(store=RedisNDLStore(cli))``."""

    def __init__(self, store: Any = None, jaccard_threshold: float = 0.75):
        self.store = store if store is not None else _DictStore()
        self.th = jaccard_threshold

    @staticmethod
    def _key(doc_type: str, field_name: str) -> str:
        return f"{doc_type}:{field_name}"

    def record(self, doc_type: str, field_name: str, context: str,
               hallucinated: str, corrected: str | None = None) -> None:
        """Persiste (o refuerza) un anti-patrón: un valor detectado como error en un contexto."""
        key = self._key(doc_type, field_name)
        ctx = frozenset(_toks(context))
        entries = self.store.load(key)
        for e in entries:
            if _norm(e.hallucinated) == _norm(hallucinated) and _jaccard(e.context, ctx) >= self.th:
                e.count += 1
                if corrected and not e.corrected:
                    e.corrected = corrected
                self.store.save(key, entries)
                return
        entries.append(AntiPattern(field_name, ctx, hallucinated, corrected))
        self.store.save(key, entries)

    def check(self, doc_type: str, field_name: str, context: str,
              value: str | None = None) -> NDLSignal:
        """¿El contexto (y opcionalmente el valor) coincide con un anti-patrón conocido?"""
        entries = self.store.load(self._key(doc_type, field_name))
        ctx = frozenset(_toks(context))
        best: AntiPattern | None = None
        for e in entries:
            if _jaccard(e.context, ctx) < self.th:
                continue
            if value is not None and _norm(value) == _norm(e.hallucinated):
                return NDLSignal(matched=True, value_hit=True, count=e.count,
                                 reason=f"valor coincide con alucinación conocida (x{e.count}) en contexto similar")
            if best is None or e.count > best.count:
                best = e
        if best is not None:
            return NDLSignal(matched=True, value_hit=False, count=best.count,
                             reason="contexto de error recurrente conocido")
        return NDLSignal(matched=False)


def apply_ndl(report: Any, ndl: NDL, doc_type: str, context: str,
              *, contract: list | None = None, learn: bool = True, reinforce: bool = True) -> Any:
    """Aplica NDL a un CoverageReport (post-cobertura). Primero REFUERZA (baja SUPPORTED→REVIEW
    si el valor es una alucinación conocida en contexto similar), luego APRENDE (registra los
    INVENTED/REVIEW como anti-patrones). Con ``contract`` recomputa el ``ok`` exacto; sin él,
    conservador (INVENTED bloquea). Devuelve un report (posiblemente) actualizado."""
    from dataclasses import replace

    from .vera_coverage import CoverageReport, _blocks_ok

    records = list(report.records)
    changed = False

    if reinforce:
        new_records = []
        for r in records:
            if (r.entity_type in _PRECISION_TYPES and r.verdict is EntityVerdict.SUPPORTED
                    and r.llm_value is not None):
                sig = ndl.check(doc_type, r.entity_name, context, str(r.llm_value))
                if sig.value_hit:
                    new_records.append(replace(
                        r, verdict=EntityVerdict.REVIEW,
                        reason=r.reason + f" [NDL: {sig.reason}]"))
                    changed = True
                    continue
            new_records.append(r)
        records = new_records

    if learn:
        for r in records:
            if (r.entity_type in _PRECISION_TYPES and r.llm_value is not None
                    and r.verdict in (EntityVerdict.INVENTED, EntityVerdict.REVIEW)):
                ndl.record(doc_type, r.entity_name, context, str(r.llm_value), r.matched_source)

    if not changed:
        return report
    if contract is not None:
        ok = not any(_blocks_ok(spec, r.verdict)
                     for spec, r in zip(contract, records, strict=True))
    else:
        ok = not any(r.verdict is EntityVerdict.INVENTED for r in records)
    return CoverageReport(ok=ok, records=records, llm_output=report.llm_output)
