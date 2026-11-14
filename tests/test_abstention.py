# Autor: @Raven
"""Tests de la política de abstención (sección 4) — deterministas, sin LLM."""
from __future__ import annotations

from pipeline.abstention import AbstentionPolicy
from pipeline.reconstructor import UNRECOVERABLE, ReconstructionResult


def _res(**kw):
    base = dict(extraction={}, mode="llm", evidence={}, uncertainty={}, abstained=[])
    base.update(kw)
    return ReconstructionResult(**base)


def test_abstiene_por_baja_confianza():
    res = _res(extraction={"A": "x", "B": "y"}, evidence={"A": "ev", "B": "ev"},
               uncertainty={"A": 0.1, "B": 0.8})
    out = AbstentionPolicy(min_confidence=0.5, require_evidence=False).apply(res, ["A", "B"])
    assert out.extraction["A"] == "x"                 # confianza 0.9 ≥ 0.5 → se queda
    assert out.extraction["B"] == UNRECOVERABLE       # confianza 0.2 < 0.5 → abstiene
    assert "B" in out.abstained


def test_abstiene_por_falta_de_evidencia():
    res = _res(extraction={"A": "x"}, evidence={"A": ""})
    out = AbstentionPolicy(min_confidence=0.0, require_evidence=True).apply(res, ["A"])
    assert out.extraction["A"] == UNRECOVERABLE


def test_politica_reconstruir_siempre_no_abstiene():
    """tau=0 + require_evidence=False → nunca abstiene, aun con confianza baja y sin evidencia."""
    res = _res(extraction={"A": "x"}, evidence={"A": ""}, uncertainty={"A": 0.99})
    out = AbstentionPolicy(min_confidence=0.0, require_evidence=False).apply(res, ["A"])
    assert out.extraction["A"] == "x"


def test_exp4_estructura_conservador():
    from experiments.exp4_abstention import run
    import tempfile, os
    res = run(n=10, tier="degraded", out_dir=os.path.join(tempfile.gettempdir(), "exp4t"))
    taus = [r["tau"] for r in res["barrido"]]
    assert taus == [0.0, 0.3, 0.5, 0.7, 0.9]
    for r in res["barrido"]:
        assert 0.0 <= r["cobertura"] <= 1.0 and 0.0 <= r["exactitud"] <= 1.0
