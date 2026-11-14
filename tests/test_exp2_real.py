# Autor: @Raven
"""Tests del Experimento 2-real (calidad de reconstrucción). Sin LLM (no gasta tokens);
verifica estructura y las relaciones HONESTAS esperadas del modo conservador."""
from __future__ import annotations

import os
import tempfile

from experiments.exp2_real import run


def test_exp2_real_oracle_y_degradacion_conservador():
    res = run(n=20, use_llm=False, out_dir=os.path.join(tempfile.gettempdir(), "e2r"))
    tiers = res["por_tier"]
    # Oracle (baseline) es exacto por construcción en todos los grados.
    for t in ("clean", "degraded", "poor"):
        assert tiers[t]["oracle"]["exactitud_campo"] == 1.0

    cons = {t: tiers[t]["conservador"] for t in ("clean", "degraded", "poor")}
    # La exactitud del conservador CAE al empeorar el OCR.
    assert cons["clean"]["exactitud_campo"] >= cons["degraded"]["exactitud_campo"] \
        >= cons["poor"]["exactitud_campo"]
    # La abstención SUBE al empeorar el OCR (abstiene en vez de inventar).
    assert cons["clean"]["tasa_abstencion"] <= cons["degraded"]["tasa_abstencion"] \
        <= cons["poor"]["tasa_abstencion"]
    # Y abstiene estrictamente más con ruido que sin ruido (no es un no-op).
    assert cons["poor"]["tasa_abstencion"] > cons["clean"]["tasa_abstencion"]
