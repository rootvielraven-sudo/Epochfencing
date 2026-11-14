# Autor: @Raven
"""Tests del Experimento 3 (VERA adversarial) — relaciones HONESTAS, sin afirmar 100%."""
from __future__ import annotations

import os
import tempfile

from pipeline.adversarial import FAMILIES, build_adversarial_cases
from pipeline.corpus import generate_corpus


def test_las_10_familias_se_generan():
    invs = generate_corpus(120, seed=1, noise_tier="degraded")
    cases = build_adversarial_cases(invs, seed=1, clean_rate=0.3)
    fams = {c.family for c in cases if c.bad}
    # Las 10 familias adversariales deben aparecer al menos una vez.
    assert set(FAMILIES).issubset(fams), set(FAMILIES) - fams
    assert any(not c.bad for c in cases)   # también hay limpias (para medir FRR)


def test_exp3_estructura_y_relaciones_honestas():
    from experiments.exp3_vera_adversarial import run
    res = run(n=120, seed=20261113, out_dir=os.path.join(tempfile.gettempdir(), "exp3t"))
    priv = res["por_fuente"]["clean"]["por_config"]
    indep = res["por_fuente"]["ocr"]["por_config"]

    # Ambas dimensiones detectan al menos tanto como una sola (monotonía del recall).
    assert priv["cobertura+calculo"]["recall"] >= priv["solo_calculo"]["recall"]
    assert indep["cobertura+calculo"]["recall"] >= indep["solo_calculo"]["recall"]

    # El cálculo NUNCA produce falsos rechazos (no lee el OCR) — en ambas fuentes.
    assert priv["solo_calculo"]["FRR"] == 0.0
    assert indep["solo_calculo"]["FRR"] == 0.0

    # La fuente INDEPENDIENTE cuesta falsos rechazos que la privilegiada esconde:
    # FRR de cobertura es estrictamente mayor con OCR degradado que con clean_text.
    assert indep["solo_cobertura"]["FRR"] > priv["solo_cobertura"]["FRR"]

    # El cálculo NO generaliza a todo: su recall es parcial (hay fallos que no rompen aritmética).
    assert indep["solo_calculo"]["recall"] < 1.0


def test_exp3_todas_las_familias_reportadas():
    from experiments.exp3_vera_adversarial import run
    res = run(n=120, seed=20261113, out_dir=os.path.join(tempfile.gettempdir(), "exp3t2"))
    assert set(res["por_fuente"]["ocr"]["familias"]) == set(FAMILIES)
