# Autor: @Raven
"""T1 semántico — verificación de entidades descriptivas por similitud (sim_fn inyectable)."""
from __future__ import annotations

import re

import pytest

from framework.vera import Source, load_contract, verify
from framework.vera.semantic import apply_semantic, cosine_sim_fn

_CONTRACT = load_contract({"entities": [{"name": "Diagnostico", "type": "text"}]})
_OCR = "El paciente presenta neumonía bilateral. Se indica reposo.\n"


def _fake_sim(a: str, b: str) -> float:
    """Coeficiente de contención determinista: |tokens(a) ∩ tokens(b)| / |tokens(a)|."""
    ta = set(re.findall(r"[a-záéíóúñ]+", a.lower()))
    tb = set(re.findall(r"[a-záéíóúñ]+", b.lower()))
    return len(ta & tb) / len(ta) if ta else 0.0


def test_text_queda_unverified_sin_t1():
    rep = verify({"Diagnostico": "neumonía bilateral"}, Source.ocr(_OCR), _CONTRACT)
    assert rep.records[0].verdict.value == "unverified"


def test_t1_ancla_descriptivo_similar():
    rep = verify({"Diagnostico": "neumonía bilateral"}, Source.ocr(_OCR), _CONTRACT)
    rep2 = apply_semantic(rep, _OCR, _CONTRACT, sim_fn=_fake_sim)     # contención = 1.0 ≥ 0.82
    assert rep2.records[0].verdict.value == "supported"
    assert "T1" in rep2.records[0].reason


def test_t1_marca_invented_descriptivo_ausente():
    rep = verify({"Diagnostico": "fractura de cadera"}, Source.ocr(_OCR), _CONTRACT)
    rep2 = apply_semantic(rep, _OCR, _CONTRACT, sim_fn=_fake_sim)     # sim baja → INVENTED
    assert rep2.records[0].verdict.value == "invented"


def test_cosine_sim_fn_builder():
    # embedder trivial one-hot por token → coseno funciona
    vocab = {"a": 0, "b": 1, "c": 2}
    def emb(s):
        v = [0.0, 0.0, 0.0]
        for t in s.split():
            if t in vocab:
                v[vocab[t]] += 1.0
        return v
    sim = cosine_sim_fn(emb)
    assert sim("a b", "a b") == pytest.approx(1.0)
    assert sim("a", "c") == 0.0
