# Autor: @Raven
"""Harness de evaluación de la dimensión de consistencia de cálculo (condición B).

- ``corpus``   : facturas sintéticas con fallos inyectados (§9 del paper) + ground truth.
- ``run_eval`` : corre la dimensión, imprime matriz de confusión y ablaciones.

Correr:  .venv/bin/python -m framework.vera.calc_eval.run_eval
"""
from __future__ import annotations

from . import corpus

__all__ = ["corpus"]
