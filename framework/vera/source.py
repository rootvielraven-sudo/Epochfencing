# Autor: @Raven
"""VERA — abstracción de FUENTE (MIKA port, sin holones).

Discrimina las dos rutas de verificación del predicado ``detect`` del paper:

  - OCR_TEXT   : imagen/PDF → transcripción OCR (string). El anclaje es por
                 PROXIMIDAD DE ETIQUETA sobre el texto (método de Minerva,
                 ``vera_coverage.match_coverage``). Si además hay ``regions``
                 (bounding boxes), el anclaje puede refinarse geométricamente
                 (mejora opcional; el texto solo ya ancla).
  - STRUCTURED : JSON / XML / FHIR / relacional / hoja de cálculo. La "región"
                 es el LOCALIZADOR de esquema (path/celda), CONOCIDO a priori:
                 la verificación es un LOOKUP determinista contra ``doc``
                 (``structured.match_structured``), no un descubrimiento.

El resto del pipeline (engine → CoverageReport) es idéntico para ambas rutas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable


class SourceKind(StrEnum):
    OCR_TEXT   = "ocr_text"
    STRUCTURED = "structured"


@dataclass(frozen=True)
class Region:
    """Región geométrica opcional para el refinamiento del anclaje OCR (Fase F).
    ``bbox`` = (x0, y0, x1, y1) en coords de página; ``page`` 1-indexed."""
    text: str
    bbox: tuple[float, float, float, float]
    page: int = 1


@dataclass(frozen=True)
class Source:
    """Fuente de verdad determinista contra la que VERA ancla las claims del LLM."""
    kind: SourceKind
    text: str | None = None                       # OCR_TEXT: transcripción
    regions: list[Region] | None = None           # OCR_TEXT: bboxes opcionales (Fase F)
    doc: Any | None = None                         # STRUCTURED: objeto parseado (dict/…)
    path_resolver: Callable[[Any, Any], Any] | None = None  # STRUCTURED: (doc, EntitySpec) → valor|None
    clean: bool = False                            # OCR_TEXT: aplicar clean stage (normalización OCR)

    # ── Constructores de conveniencia ────────────────────────────────────────
    @staticmethod
    def ocr(text: str, regions: list[Region] | None = None, clean: bool = False) -> "Source":
        """Fuente OCR (imagen/PDF ya transcripto). ``regions`` opcional para geometría;
        ``clean=True`` normaliza confusiones OCR en tokens numéricos antes de anclar."""
        return Source(kind=SourceKind.OCR_TEXT, text=text or "", regions=regions, clean=clean)

    @staticmethod
    def structured(doc: Any, path_resolver: Callable[[Any, Any], Any] | None = None) -> "Source":
        """Fuente estructurada (JSON/XML/FHIR/relacional). ``path_resolver`` opcional:
        (doc, EntitySpec) → valor de la fuente en la ruta declarada, o None si ausente.
        Sin resolver, se usa el lookup por clave/nombre/alias de ``structured.py``."""
        return Source(kind=SourceKind.STRUCTURED, doc=doc, path_resolver=path_resolver)
