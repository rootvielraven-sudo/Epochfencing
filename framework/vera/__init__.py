# Autor: @Raven
"""VERA — verificación determinista de extracciones LLM (MIKA port de Minerva, sin holones).

Dos rutas del predicado ``detect`` del paper:
  - OCR:        anclaje por proximidad de etiqueta sobre la transcripción (match_coverage).
  - Estructurada: lookup determinista contra path/celda (match_structured).

Uso:
    from framework.vera import verify, Source, load_contract
    contract = load_contract(cfg)              # familias por entidad (SemanticType)
    report   = verify(llm_output, Source.ocr(texto_ocr), contract)
    report.ok, report.records, report.notify   # veredicto por entidad
"""
from __future__ import annotations

from .adapters import regions_from_azure, retvec_sim_fn
from .clean import clean_ocr_text
from .engine import check_form, verify
from .ndl import NDL, RedisNDLStore, apply_ndl
from .regions import build_transcript, geometric_anchor, hybrid_coverage
from .semantic import apply_semantic, cosine_sim_fn
from .source import Region, Source, SourceKind
from .structured import match_structured
from .vera_coverage import (
    CoverageRecord,
    CoverageReport,
    load_contract,
    match_coverage,
)
from .vera_schema import (
    EntitySpec,
    SemanticType,
    build_llm_schema,
    build_prompt_fragment,
    build_type_map,
    contract_fingerprint,
    serialize_contract,
    validate_output,
)
from .vera_types import EntityVerdict

__all__ = [
    "verify", "check_form",
    "Source", "SourceKind", "Region",
    "match_coverage", "match_structured",
    "hybrid_coverage", "geometric_anchor", "build_transcript",
    "NDL", "RedisNDLStore", "apply_ndl", "apply_semantic", "cosine_sim_fn",
    "regions_from_azure", "retvec_sim_fn", "clean_ocr_text",
    "CoverageReport", "CoverageRecord",
    "EntitySpec", "SemanticType", "EntityVerdict",
    "load_contract", "build_llm_schema", "build_type_map",
    "build_prompt_fragment", "serialize_contract", "contract_fingerprint",
    "validate_output",
]
