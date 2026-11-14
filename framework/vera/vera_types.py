# Autor: @Raven
"""VERA — tipos NEUTRALES compartidos (sin dependencia de capas).

Hogar canónico de ``EntityVerdict``: lo usan tanto la capa de COBERTURA (estructurado) como el
Tribunal de Evidencia (texto). Vivía en ``vera_tribunal`` y la cobertura lo importaba de ahí —
una dependencia de capa equivocada. Acá no depende de ninguna capa; ambas importan de este módulo.
"""
from __future__ import annotations

from enum import StrEnum


class EntityVerdict(StrEnum):
    """Veredicto POR ENTIDAD (cara a producto), separado del FigureStatus interno del tribunal.

    SUPPORTED  : ancló — confirmado contra el OCR (hay match a la vista).
    INVENTED   : evaluado y NO ancla (error duro) → bloqueo.
    REVIEW     : mirado pero dudoso — ambiguo / colisión / rol / corrupción de OCR → revisión.
    UNVERIFIED : nadie lo miró (ausencia de finding) — lo emite la CAPA DE COBERTURA cuando una
                 entidad declarada en el contrato no tiene anclaje (null o tipo sin extractor).
    """
    SUPPORTED  = "supported"
    INVENTED   = "invented"
    REVIEW     = "review"
    UNVERIFIED = "unverified"
