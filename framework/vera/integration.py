# Autor: @Raven
"""VERA — adaptador de INTEGRACIÓN con el pipeline de MIKA (opt-in, inerte por defecto).

Puntos de enganche:
  - Extracción documental: ``app/api/process_api.py`` (tras la extracción del LLM).
  - RAG: ``framework/chatBotv2/pipeline_v2/compliance/pipeline_guard._run_vera``.

Regla de oro: si el flag ``VERA_ENGINE`` != "minerva" o no hay contrato para el
doc_type, ``verify_extraction`` devuelve ``None`` → el caller sigue con el camino
legacy (ValidacionFuzzy). Cero cambio de comportamiento por defecto.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from .contract import load_contract_for
from .engine import verify
from .source import Source
from .structured import _default_resolver
from .vera_coverage import CoverageReport
from .vera_schema import EntitySpec
from .vera_types import EntityVerdict

logger = logging.getLogger(__name__)


def vera_engine_enabled() -> bool:
    """True si el motor VERA (port de Minerva) está activo por env: ``VERA_ENGINE=minerva``."""
    return os.getenv("VERA_ENGINE", "legacy").strip().lower() == "minerva"


def vera_mode() -> str:
    """Modo de operación: ``shadow`` (default, solo verifica y loguea) | ``authoritative``
    (adjunta el veredicto al output). Env ``VERA_MODE``."""
    return os.getenv("VERA_MODE", "shadow").strip().lower()


def attach_report(result: Any, report: CoverageReport) -> tuple[Any, bool]:
    """Adjunta el veredicto VERA al output de forma NO DESTRUCTIVA (agrega la clave
    ``_vera`` a un dict; deja el resto intacto). Devuelve ``(result, blocked)`` donde
    ``blocked`` es True si hay alguna alucinación dura (INVENTED). No corrige valores en
    sitio: la evidencia/fuente viaja en ``_vera.findings`` para revisión/corrección aguas
    abajo (trazabilidad por construcción)."""
    # Correcciones sugeridas: entidades no-SUPPORTED cuya fuente determinista SÍ se conoce
    # (divergencia). NO se aplica en sitio (el output puede ser anidado); viaja como dato.
    corrections = [
        {"campo": r.entity_name, "de": r.llm_value, "a": r.matched_source}
        for r in report.notify if r.matched_source is not None
    ]
    summary = {
        "ok": report.ok,
        "counts": report.counts(),
        "findings": [
            {"campo": r.entity_name, "tipo": r.entity_type.value, "valor": r.llm_value,
             "veredicto": r.verdict.value, "motivo": r.reason,
             "fuente": r.matched_source, "evidencia": r.evidence}
            for r in report.notify
        ],
        "corrections": corrections,
    }
    blocked = any(r.verdict is EntityVerdict.INVENTED for r in report.records)
    if isinstance(result, dict):
        result = {**result, "_vera": summary}
    return result, blocked


def map_extraction(extraction: Any, contract: list[EntitySpec]) -> dict[str, str | None]:
    """Mapea un output de extracción (dict anidado header+items, o ya plano) a las
    claves-slug del contrato, buscando cada entidad por clave/nombre/alias (case-insensitive,
    anidado). Ausente → None. Es el puente entre el esquema del LLM y el del contrato."""
    out: dict[str, str | None] = {}
    for spec in contract:
        v = _default_resolver(extraction, spec)
        out[spec.key] = None if v is None else str(v)
    return out


def verify_extraction(
    llm_output: dict[str, Any],
    ocr_text: str,
    *,
    doc_type: str | None = None,
    client: str = "default",
    contract: list[EntitySpec] | None = None,
    map_output: bool = True,
    ndl: Any = None,
    sim_fn: Any = None,
    regions: Any = None,
) -> CoverageReport | None:
    """Verifica el output del LLM contra el OCR usando el contrato del doc_type.

    Devuelve ``None`` (→ caller usa legacy) si el motor está apagado o no hay contrato.
    Con ``map_output=True`` (default) el ``llm_output`` puede venir en el esquema crudo del
    LLM (anidado): se mapea a las claves-slug del contrato automáticamente.

    Capas opcionales (default off): ``regions`` (bboxes → anclaje MIXTO geométrico+plano),
    ``sim_fn`` (T1 semántico para entidades TEXT), ``ndl`` (memoria diacrónica de
    anti-patrones; usa el OCR como contexto).
    """
    if contract is None:
        if not vera_engine_enabled():
            return None
        contract = load_contract_for(doc_type, client=client)
    if not contract:
        return None
    try:
        output = map_extraction(llm_output, contract) if map_output else llm_output
        report = verify(output, Source.ocr(ocr_text, regions=regions), contract)
        if sim_fn is not None:
            from .semantic import apply_semantic
            report = apply_semantic(report, ocr_text, contract, sim_fn=sim_fn)
        if ndl is not None:
            from .ndl import apply_ndl
            report = apply_ndl(report, ndl, doc_type or "", ocr_text, contract=contract)
        return report
    except Exception as e:  # nunca romper el pipeline por la verificación
        logger.warning("VERA(minerva) verify_extraction falló, se ignora: %s", e)
        return None


def report_to_legacy(report: CoverageReport) -> Any:
    """Mapea un CoverageReport al formato que consume el pipeline legacy:
    ``"OK"`` si todo SUPPORTED/ok, o un dict con los hallazgos no-soportados."""
    if report.ok and not report.notify:
        return "OK"
    findings = [
        {
            "valor": r.llm_value,
            "ruta": r.entity_name,
            "estado": r.verdict.value,          # supported/invented/review/unverified
            "motivo": r.reason,
            "fuente": r.matched_source,          # valor correcto de la fuente (si divergencia)
        }
        for r in report.notify
    ]
    return {
        "valores_llm_no_en_ocr": [f for f in findings if f["estado"] != EntityVerdict.SUPPORTED.value],
        "estadisticas": report.counts(),
        "ok": report.ok,
    }
