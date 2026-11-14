# Autor: @Raven
"""Tests del pipeline de recuperación y de la compuerta VERA."""
from __future__ import annotations

import os
import tempfile

from pipeline.corpus import generate_corpus
from pipeline.faults import ContentFaultPlan, hallucination, silent_corruption
from pipeline.gate import VeraGate
from pipeline.model import InvoiceState, Record
from pipeline.store import EpochFencedStore


def test_epoch_fencing_rechaza_zombi():
    with tempfile.TemporaryDirectory() as d:
        st = EpochFencedStore(d, fence_epochs=True)
        assert st.commit(Record("F1", 2, "w", {"x": 1})).accepted
        out = st.commit(Record("F1", 1, "zombi", {"x": 99}))      # epoch viejo
        assert not out.accepted and out.reason == "stale_epoch"
        assert st.get("F1").extraction == {"x": 1}                # no lo pisó
        assert st.stale_overwrites == 0


def test_sin_fencing_el_zombi_pisa():
    with tempfile.TemporaryDirectory() as d:
        st = EpochFencedStore(d, fence_epochs=False)
        assert st.commit(Record("F1", 2, "w", {"x": 1})).accepted
        assert st.commit(Record("F1", 1, "zombi", {"x": 99})).accepted
        assert st.get("F1").extraction == {"x": 99}               # corrupción
        assert st.stale_overwrites == 1


def test_commit_idempotente_mismo_epoch():
    with tempfile.TemporaryDirectory() as d:
        st = EpochFencedStore(d, fence_epochs=True)
        st.commit(Record("F1", 1, "w", {"x": 1}))
        out = st.commit(Record("F1", 1, "w", {"x": 1}))
        assert out.accepted and out.reason == "duplicate"


def test_gate_detecta_alucinacion_y_corrupcion():
    inv = generate_corpus(1, seed=1, noise_tier=None)[0]
    gate = VeraGate(enabled=True)
    import random
    rng = random.Random(0)
    # limpio → verified
    assert gate.evaluate(inv["truth"], inv["clean_text"]).state == "verified"
    # alucinación (cobertura) → degraded
    hal = hallucination(inv["truth"], rng)
    assert gate.evaluate(hal, inv["clean_text"]).state == "degraded"
    # corrupción (cálculo) → degraded
    cor = silent_corruption(inv["truth"], rng)
    assert gate.evaluate(cor, inv["clean_text"]).state == "degraded"


def test_exp1_invariantes():
    from experiments.exp1_node_crash import run
    res = run(n=30, crash_fraction=0.3, out_dir=os.path.join(tempfile.gettempdir(), "exp1t"))
    none = res["by_mode"]["none"]
    recl = res["by_mode"]["reclaim"]
    epoch = res["by_mode"]["reclaim+epoch"]
    assert none["facturas_perdidas"] > 0                 # sin recuperación se pierde trabajo
    assert recl["facturas_perdidas"] == 0                # la reclamación recupera
    assert recl["facturas_duplicadas"] > 0               # pero el zombi duplica
    assert epoch["facturas_duplicadas"] == 0             # el fencing elimina duplicados
    assert epoch["pdf_igual_a_sin_fallos"] is True       # y el PDF coincide con la corrida limpia


def test_exp2_vera_vs_sin_vera():
    from experiments.exp2_content import run
    res = run(n=40, out_dir=os.path.join(tempfile.gettempdir(), "exp2t"))
    assert res["con_vera"]["tasa_deteccion"] == 1.0
    assert res["con_vera"]["falsos_negativos"] == 0
    assert res["sin_vera"]["tasa_deteccion"] == 0.0      # sin VERA no se detecta nada
    assert res["sin_vera"]["falsos_negativos"] == res["con_vera"]["malas"]
