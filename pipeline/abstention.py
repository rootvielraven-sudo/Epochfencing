# Autor: @Raven
"""Criterio de ABSTENCIÓN basado en evidencia y confianza (sección 4).

Encuadre honesto de la Precondición de Reconstrucción (Prop. 1 del paper):

    R recupera s solo si  I(ĉ; s) > I_min(φ, ε).

I(ĉ; s) es una cantidad **poblacional** (información mutua entre la observación degradada ĉ y
la secuencia latente s). NO la medimos por documento: no hay un estimador insesgado de IM a
partir de UN par (ĉ, s) sin un modelo de densidad, y fabricar un "número de IM por factura"
sería pseudo-ciencia. Lo que SÍ es observable y accionable por documento es:

  - EVIDENCIA disponible      : ¿el valor ancla en la fuente (ĉ)? (señal determinista de VERA).
  - INCERTIDUMBRE predictiva  : 1 − confianza que reporta el reconstructor (si la reporta).
  - FIABILIDAD calibrada      : a nivel corpus, con qué frecuencia "confianza alta" acierta.

Este módulo implementa la CAPACIDAD DE ABSTENCIÓN: en vez de muestrear del prior (alucinar),
el sistema emite ``[UNRECOVERABLE]`` cuando la evidencia falta o la confianza es baja. La
recuperabilidad poblacional se mide EMPÍRICAMENTE a nivel corpus (exp2_real: a más degradación,
menos recuperación), no como un falso "I por documento".
"""
from __future__ import annotations

from dataclasses import dataclass

from .reconstructor import UNRECOVERABLE, ReconstructionResult


@dataclass
class AbstentionPolicy:
    """Decide abstención por campo. ``min_confidence`` = umbral τ (abstiene si confianza < τ);
    ``require_evidence`` abstiene si el reconstructor no aportó fragmento de evidencia.

    τ = 0 y require_evidence = False → política de "RECONSTRUIR SIEMPRE" (nunca abstiene),
    el punto de comparación que pide la sección 4."""
    min_confidence: float = 0.0
    require_evidence: bool = True

    def apply(self, result: ReconstructionResult, field_names: list[str]) -> ReconstructionResult:
        ext = dict(result.extraction)
        abst = list(result.abstained)
        for name in field_names:
            v = ext.get(name)
            if v is None or (isinstance(v, str) and v.strip() == UNRECOVERABLE):
                ext[name] = UNRECOVERABLE
                if name not in abst:
                    abst.append(name)
                continue
            confidence = 1.0 - result.uncertainty.get(name, 0.0)   # sin dato → confianza 1.0
            evidence = result.evidence.get(name, "")
            if confidence < self.min_confidence or (self.require_evidence and not evidence):
                ext[name] = UNRECOVERABLE
                if name not in abst:
                    abst.append(name)
        return ReconstructionResult(extraction=ext, mode=result.mode, evidence=result.evidence,
                                    uncertainty=result.uncertainty, abstained=abst)
