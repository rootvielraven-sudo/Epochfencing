# Autor: @Raven
"""VERA — harness de evaluación (mide, no fabrica).

Computa matrices de confusión / capture / FP / F1 con IC Wilson a partir de casos
ETIQUETADOS (gold por campo). No inventa números: si no hay corpus anotado, no hay
métricas. Ver ``harness.evaluate`` y ``harness.write_csvs``.
"""
from .harness import EvalCase, evaluate, wilson_ci, write_csvs

__all__ = ["EvalCase", "evaluate", "wilson_ci", "write_csvs"]
