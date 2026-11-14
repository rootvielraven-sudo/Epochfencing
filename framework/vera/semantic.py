# Autor: @Raven
"""VERA — capa semántica T1 (paper §5.6): similitud coseno para entidades descriptivas.

Las entidades de tipo TEXT (prosa libre: diagnóstico, concepto, resumen) no tienen anclaje
determinista → el plano las deja UNVERIFIED. T1 las verifica por SIMILITUD SEMÁNTICA contra
la fuente: se parte la fuente en oraciones y se toma la máxima similitud coseno entre la
claim y una oración. θ=0.82 → SUPPORTED, θ_min=0.65 → REVIEW, por debajo → INVENTED.

La función de similitud es INYECTABLE (``sim_fn(claim, texto)→[0,1]``): en tests se usa una
fake determinista; en producción se construye con ``cosine_sim_fn(embed_fn)`` sobre el
embedder que ya usa MIKA (framework/Embeddings). Sin ``sim_fn`` configurada, T1 no actúa y
las entidades TEXT siguen UNVERIFIED (degradación honesta).
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Callable

from .vera_schema import EntitySpec, SemanticType
from .vera_types import EntityVerdict

SimFn = Callable[[str, str], float]

THETA = 0.82
THETA_MIN = 0.65


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[.!?;\n]\s*", text or "")
    return [p.strip() for p in parts if p.strip()]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def cosine_sim_fn(embed_fn: Callable[[str], list[float]]) -> SimFn:
    """Construye una ``sim_fn`` coseno a partir de un embedder ``embed_fn(str)→vector``.
    Producción: pasarle el embedder de MIKA (OpenAI / RetVec)."""
    def sim(a: str, b: str) -> float:
        return _cosine(embed_fn(a), embed_fn(b))
    return sim


def apply_semantic(report: Any, source_text: str, contract: list[EntitySpec],
                   *, sim_fn: SimFn, theta: float = THETA, theta_min: float = THETA_MIN) -> Any:
    """Verifica por T1 las entidades TEXT que quedaron UNVERIFIED. Devuelve el report
    actualizado (recomputa ``ok`` con el contrato)."""
    from .vera_coverage import CoverageReport, _blocks_ok

    sents = _sentences(source_text)
    records = list(report.records)
    changed = False
    new_records = []
    for spec, r in zip(contract, records, strict=True):
        if (spec.type is SemanticType.TEXT and r.verdict is EntityVerdict.UNVERIFIED
                and r.llm_value is not None):
            best = max((sim_fn(str(r.llm_value), s) for s in sents), default=0.0)
            if best >= theta:
                verdict, reason = EntityVerdict.SUPPORTED, f"T1 semántico: sim={best:.2f} ≥ {theta}"
            elif best >= theta_min:
                verdict, reason = EntityVerdict.REVIEW, f"T1 semántico: sim={best:.2f} en zona de revisión"
            else:
                verdict, reason = EntityVerdict.INVENTED, f"T1 semántico: sim={best:.2f} < {theta_min}"
            new_records.append(replace(r, verdict=verdict, reason=reason))
            changed = True
        else:
            new_records.append(r)

    if not changed:
        return report
    ok = not any(_blocks_ok(spec, rr.verdict)
                 for spec, rr in zip(contract, new_records, strict=True))
    return CoverageReport(ok=ok, records=new_records, llm_output=report.llm_output)
