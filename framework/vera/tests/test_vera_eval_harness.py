# Autor: @Raven
"""Test del harness de evaluación (datos SINTÉTICOS etiquetados — no son del paper)."""
from __future__ import annotations

import os

from framework.vera import Source, load_contract
from framework.vera.eval import EvalCase, evaluate, wilson_ci, write_csvs

_CONTRACTS = {
    "factura": load_contract({"entities": [
        {"name": "Total", "type": "number"},
        {"name": "Fecha", "type": "date"},
    ]}),
}

_OCR = "Total: 100,00\nFecha: 01/01/2024\n"


def _cases():
    return [
        # correcto: ambos anclan → SUPPORTED → no flagged
        EvalCase("factura", Source.ocr(_OCR),
                 {"Total": "100.00", "Fecha": "2024-01-01"},
                 {"Total": "correct", "Fecha": "correct"}),
        # alucinado: Total no está en el OCR → INVENTED → flagged
        EvalCase("factura", Source.ocr(_OCR),
                 {"Total": "999999,99", "Fecha": "2024-01-01"},
                 {"Total": "hallucinated", "Fecha": "correct"}),
    ]


def test_wilson_ci_basic():
    lo, hi = wilson_ci(1, 1)
    assert 0.0 <= lo <= hi <= 1.0
    assert wilson_ci(0, 0) == (0.0, 0.0)


def test_evaluate_counts_are_deterministic():
    m = evaluate(_cases(), _CONTRACTS)
    ov = m["overall"]
    # Total: 1 correct(TN) + 1 hallucinated capturado(TP); Fecha: 2 correct(TN)
    assert ov["TP"] == 1 and ov["FP"] == 0 and ov["FN"] == 0 and ov["TN"] == 3
    assert ov["recall_capture"] == 1.0     # capturó la única alucinación
    assert ov["fp_rate"] == 0.0            # cero falsos positivos
    assert ov["N"] == 4


def test_evaluate_groups_present():
    m = evaluate(_cases(), _CONTRACTS)
    assert "factura" in m["by_doc_type"]
    assert "number" in m["by_entity_type"] and "date" in m["by_entity_type"]


def test_write_csvs(tmp_path):
    m = evaluate(_cases(), _CONTRACTS)
    paths = write_csvs(m, str(tmp_path / "vera_eval"))
    assert len(paths) == 3
    for p in paths:
        assert os.path.isfile(p) and os.path.getsize(p) > 0
