# Autor: @Raven
"""Tests del corpus REAL (Desktop/datos-demo). Se SALTEAN si no está el directorio."""
from __future__ import annotations

import os
import shutil

import pytest

from pipeline.real_corpus import DEMO_DIR, load_real_corpus

_has_demo = os.path.isdir(DEMO_DIR) and shutil.which("pdftotext")
pytestmark = pytest.mark.skipif(not _has_demo, reason="datos-demo o pdftotext no disponibles")


def test_carga_facturas_reales():
    inv = load_real_corpus(noise_tier=None, limit=20)
    assert len(inv) > 0
    for i in inv:
        assert i["truth"].get("Total a pagar")
        assert i["clean_text"] and i["ocr_text"]


def test_reales_limpias_pasan_vera():
    """Las facturas reales reconstruidas correctamente no deben dar falsos positivos."""
    from pipeline.gate import VeraGate
    inv = load_real_corpus(noise_tier=None, limit=30)
    gate = VeraGate(enabled=True)
    fp = sum(1 for i in inv if gate.evaluate(i["truth"], i["clean_text"]).state != "verified")
    assert fp == 0, f"{fp} falsos positivos sobre facturas reales limpias"


def test_exp2_sobre_reales():
    from experiments.exp2_content import run
    inv = load_real_corpus(noise_tier="degraded", limit=40)
    res = run(invoices=inv, out_dir=os.path.join("/tmp", "exp2real"))
    assert res["con_vera"]["falsos_positivos"] == 0
    assert res["con_vera"]["tasa_deteccion"] == 1.0
    assert res["sin_vera"]["tasa_deteccion"] == 0.0
