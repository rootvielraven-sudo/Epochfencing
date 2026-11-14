# Autor: @Raven
"""VERA — cableado de la dimensión de CONTRADICCIÓN en el VEREDICTO COMBINADO.

Cubre el cambio de MOTOR: la dimensión ``CONTRADICTED`` del tribunal de cifras ahora
participa del ``combine`` de ``vera_verdict`` detrás del flag opt-in ``VERA_CONTRADICTION``
(default OFF). Las propiedades que se garantizan:

  - Flag OFF  → ``combine`` es IDÉNTICO al histórico (cobertura+cálculo), aunque se le pase
    ``contradiction`` (regresión).
  - Flag ON + una cifra CONTRADICTED → el veredicto combinado degrada a ``blocked`` (NO
    "aceptar/coherente").
  - Flag ON + sin contradicción → la dimensión SE ABSTIENE y ``combine`` vuelve a ser
    cobertura+cálculo (mismo dict de siempre).
  - El estado de contradicción se deriva del tribunal REAL (jueces duros deterministas),
    no de un mock: solo dispara cuando hay CONTRADICTED con evidencia.
"""
from __future__ import annotations

import pytest

from framework.vera.vera_tribunal import build_contradiction_router
from framework.vera.vera_verdict import (
    combine,
    contradiction_enabled,
    contradiction_state,
    overall_state,
)


# ─────────────────────────── fixtures de flag ────────────────────────────────

@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.delenv("VERA_CONTRADICTION", raising=False)
    assert contradiction_enabled() is False
    return False


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setenv("VERA_CONTRADICTION", "on")
    assert contradiction_enabled() is True
    return True


# ─────────────────────── flag: lectura de la env ─────────────────────────────

@pytest.mark.parametrize("val,expected", [
    ("on", True), ("1", True), ("true", True), ("TRUE", True), ("  On ", True),
    ("off", False), ("0", False), ("false", False), ("", False), ("no", False),
])
def test_flag_lectura_env(monkeypatch, val, expected):
    monkeypatch.setenv("VERA_CONTRADICTION", val)
    assert contradiction_enabled() is expected


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("VERA_CONTRADICTION", raising=False)
    assert contradiction_enabled() is False


# ───────────────── regresión: flag OFF = comportamiento histórico ─────────────

def test_combine_flag_off_ignora_contradiccion(flag_off):
    """Con el flag OFF, pasar ``contradiction='blocked'`` NO cambia nada: el veredicto
    combinado es exactamente el de cobertura+cálculo de siempre."""
    base = combine(coverage="clean", calc="clean")
    con_contra = combine(coverage="clean", calc="clean", contradiction="blocked")
    assert con_contra == base
    assert con_contra["state"] == "clean"
    assert con_contra["blocked"] is False
    assert con_contra["dimensions"] == {"coverage": "clean", "calc": "clean"}
    assert "contradiction" not in con_contra["dimensions"]


def test_combine_flag_off_sin_contradiccion_identico(flag_off):
    """Sanity: con el flag OFF y sin pasar contradicción, el dict es el histórico."""
    v = combine(coverage="review", calc="clean")
    assert v["state"] == "review"
    assert v["dimensions"] == {"coverage": "review", "calc": "clean"}


# ──────────────────── flag ON + contradicción demostrada ─────────────────────

def test_combine_flag_on_contradiccion_bloquea(flag_on):
    """Una cifra CONTRADICTED impide 'aceptar': el combinado degrada a ``blocked``."""
    v = combine(coverage="clean", calc="clean", contradiction="blocked")
    assert v["state"] == "blocked"
    assert v["blocked"] is True
    assert v["dimensions"]["contradiction"] == "blocked"
    assert v["dimensions"]["coverage"] == "clean"
    assert v["dimensions"]["calc"] == "clean"


def test_combine_flag_on_contradiccion_el_mas_severo_gana(flag_on):
    """La contradicción es una dimensión más: el más severo gana (no la relaja)."""
    # cobertura limpia + contradicción bloqueante → blocked
    assert combine(coverage="clean", calc=None, contradiction="blocked")["state"] == "blocked"
    # cobertura ya bloqueada + contradicción → sigue blocked (no cambia la severidad)
    assert combine(coverage="blocked", calc=None, contradiction="blocked")["state"] == "blocked"


def test_combine_flag_on_contradiccion_sola_surface(flag_on):
    """La contradicción puede surgir SOLA (sin cobertura ni cálculo) y bloquear."""
    v = combine(coverage=None, calc=None, contradiction="blocked")
    assert v["state"] == "blocked"
    assert v["dimensions"]["contradiction"] == "blocked"


# ──────────────────── flag ON + sin contradicción = abstención ───────────────

def test_combine_flag_on_abstiene_es_identico_a_coverage_calc(flag_on):
    """Flag ON pero la dimensión se abstiene (contradiction=None): el dict es EXACTAMENTE
    el de cobertura+cálculo de siempre (no agrega la clave 'contradiction')."""
    base = combine(coverage="clean", calc="clean")
    v = combine(coverage="clean", calc="clean", contradiction=None)
    assert v == base
    assert v["state"] == "clean"
    assert v["dimensions"] == {"coverage": "clean", "calc": "clean"}
    assert "contradiction" not in v["dimensions"]


def test_combine_flag_on_abstiene_no_degrada_review(flag_on):
    """Si la dimensión se abstiene, no fuerza revisión ni bloqueo: respeta lo demás."""
    v = combine(coverage="review", calc="clean", contradiction=None)
    assert v["state"] == "review"
    assert "contradiction" not in v["dimensions"]


# ─────────────────────────── overall_state directo ──────────────────────────

def test_overall_state_contradiccion():
    assert overall_state(coverage="clean", calc="clean", contradiction="blocked") == "blocked"
    assert overall_state(coverage="clean", calc="clean", contradiction=None) == "clean"
    assert overall_state(coverage="review", calc=None, contradiction=None) == "review"


# ─────────────── contradiction_state a partir del TribunalResult ─────────────

def test_contradiction_state_none_cuando_no_hay_tribunal():
    assert contradiction_state(None) is None


def test_contradiction_state_blocked_con_cifra_corrompida():
    """Tribunal REAL: un monto del output choca con la fuente dentro de la banda 20%
    (misma cifra corrompida) → CONTRADICTED → la dimensión bloquea."""
    router = build_contradiction_router()
    source = "Importe Total: 1000.00"
    output = "Importe Total: 1100.00"   # +10% → misma cifra corrompida (banda 20%)
    res = router.run(output, source, tipo_documento="")
    # hay evidencia de CONTRADICTED en el tribunal
    assert any(f.status == "contradicted" for f in res.blocking_evidence)
    assert contradiction_state(res) == "blocked"


def test_contradiction_state_abstiene_cuando_coincide():
    """Tribunal REAL: el monto del output coincide con la fuente → SUPPORTED, sin
    CONTRADICTED → la dimensión se abstiene (None)."""
    router = build_contradiction_router()
    source = "Importe Total: 1000.00"
    output = "Importe Total: 1000.00"
    res = router.run(output, source, tipo_documento="")
    assert not any(f.status == "contradicted" for f in res.blocking_evidence)
    assert contradiction_state(res) is None


def test_contradiction_state_abstiene_sin_cifras():
    """Sin cifras que comparar, el tribunal no marca CONTRADICTED → abstención."""
    router = build_contradiction_router()
    res = router.run("sin montos aquí", "tampoco hay montos en la fuente", tipo_documento="")
    assert contradiction_state(res) is None


# ───────────── integración: tribunal real → combine detrás del flag ──────────

def _run_tribunal(output: str, source: str):
    return build_contradiction_router().run(output, source, tipo_documento="")


def test_e2e_flag_off_contradiccion_real_no_participa(flag_off):
    """Caso NEGATIVO/aislamiento: aunque el tribunal marque CONTRADICTED, con el flag OFF
    la dimensión NO participa del combinado (regresión = cobertura+cálculo)."""
    res = _run_tribunal("Importe Total: 1100.00", "Importe Total: 1000.00")
    contra = contradiction_state(res)            # el tribunal SÍ ve la contradicción…
    assert contra == "blocked"
    v = combine(coverage="clean", calc="clean", contradiction=contra)  # …pero flag OFF
    assert v["state"] == "clean"
    assert "contradiction" not in v["dimensions"]


def test_e2e_flag_on_contradiccion_real_bloquea(flag_on):
    res = _run_tribunal("Importe Total: 1100.00", "Importe Total: 1000.00")
    contra = contradiction_state(res)
    assert contra == "blocked"
    v = combine(coverage="clean", calc="clean", contradiction=contra)
    assert v["state"] == "blocked" and v["blocked"] is True


def test_e2e_flag_on_sin_contradiccion_real_abstiene(flag_on):
    res = _run_tribunal("Importe Total: 1000.00", "Importe Total: 1000.00")
    contra = contradiction_state(res)
    assert contra is None
    v = combine(coverage="clean", calc="clean", contradiction=contra)
    assert v == combine(coverage="clean", calc="clean")
    assert v["state"] == "clean"
