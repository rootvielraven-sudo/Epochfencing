# Autor: @Raven
"""ADR-022 pilar de cobertura — PUNTO 1: EntityVerdict + agregador de cobertura cerrada.

Verifica:
  - clasificación por entidad (SUPPORTED / INVENTED / UNVERIFIED).
  - TRANSPARENCIA con contrato vacío: idéntico al comportamiento histórico.
  - COBERTURA CERRADA con contrato declarado: nunca OK por ausencia de findings;
    OK solo si cada nombre declarado tiene un SUPPORTED que lo ancla.
"""
from __future__ import annotations

from framework.vera.vera_tribunal import (
    EntityVerdict,
    FigureFinding,
    FigureStatus,
    JudgeVerdict,
    PanelVerdict,
    aggregate,
    classify_entities,
)


def _hard(status: FigureStatus, name: str = "") -> JudgeVerdict:
    f = FigureFinding(output_raw="x", status=status)
    if name:
        f.entity_name = name  # type: ignore[attr-defined]  # role-tag (punto 3); acá lo simulamos
    return JudgeVerdict(judge_id="numeric_integrity", is_hard=True, findings=[f])


def _soft(status: FigureStatus) -> JudgeVerdict:
    return JudgeVerdict(judge_id="lexical_grounding", is_hard=False,
                        findings=[FigureFinding(output_raw="x", status=status)])


# ── clasificación por entidad ────────────────────────────────────────────────
def test_classify_entities_mapping() -> None:
    vs = [
        _hard(FigureStatus.SUPPORTED),
        _hard(FigureStatus.CONTRADICTED),
        _hard(FigureStatus.NOT_FOUND),
        _hard(FigureStatus.AMBIGUOUS),
        _soft(FigureStatus.NOT_FOUND),     # heurístico con 'error' → revisión, no bloqueo
    ]
    got = [ev for ev, _ in classify_entities(vs)]
    assert got == [
        EntityVerdict.SUPPORTED,
        EntityVerdict.INVENTED,
        EntityVerdict.INVENTED,
        EntityVerdict.REVIEW,       # AMBIGUOUS (juez duro) → REVIEW (la miró, dudosa)
        EntityVerdict.REVIEW,       # NOT_FOUND de juez heurístico → REVIEW (no INVENTED)
    ]
    # Punto 1.5: classify_entities NUNCA emite UNVERIFIED (eso es ausencia → capa cobertura)
    assert EntityVerdict.UNVERIFIED not in got


# ── contrato VACÍO → transparente (idéntico al histórico) ─────────────────────
def test_empty_contract_clean_is_ok() -> None:
    v, blk, rev = aggregate([_hard(FigureStatus.SUPPORTED)])
    assert v is PanelVerdict.OK and not blk and not rev


def test_empty_contract_hard_error_blocks() -> None:
    v, blk, rev = aggregate([_hard(FigureStatus.CONTRADICTED)])
    assert v is PanelVerdict.HALLUCINATION and len(blk) == 1


def test_empty_contract_review_warns() -> None:
    v, blk, rev = aggregate([_hard(FigureStatus.AMBIGUOUS)])
    assert v is PanelVerdict.WARNING and not blk and len(rev) == 1


def test_empty_contract_no_findings_is_ok() -> None:
    # comportamiento histórico: sin findings y sin contrato → OK (transparencia)
    v, blk, rev = aggregate([])
    assert v is PanelVerdict.OK


# ── contrato DECLARADO → cobertura cerrada (nunca OK por ausencia) ────────────
def test_contract_declared_but_unanchored_is_not_ok() -> None:
    # SUPPORTED sin role-tag → no ancla el nombre declarado → NUNCA OK → revisión
    v, blk, rev = aggregate([_hard(FigureStatus.SUPPORTED)], contract=["total a pagar"])
    assert v is PanelVerdict.WARNING


def test_contract_fully_supported_is_ok() -> None:
    # SUPPORTED con role-tag que matchea el nombre declarado → cobertura completa → OK
    v, blk, rev = aggregate([_hard(FigureStatus.SUPPORTED, name="total a pagar")],
                            contract=["total a pagar"])
    assert v is PanelVerdict.OK


def test_contract_hard_error_still_blocks() -> None:
    v, blk, rev = aggregate([_hard(FigureStatus.CONTRADICTED, name="total a pagar")],
                            contract=["total a pagar"])
    assert v is PanelVerdict.HALLUCINATION


def test_contract_partial_coverage_is_not_ok() -> None:
    # un nombre anclado, otro declarado-ausente → no OK
    v, blk, rev = aggregate([_hard(FigureStatus.SUPPORTED, name="total a pagar")],
                            contract=["total a pagar", "IBAN"])
    assert v is PanelVerdict.WARNING


def test_contract_accepts_dict_entries() -> None:
    v, blk, rev = aggregate([_hard(FigureStatus.SUPPORTED, name="IBAN")],
                            contract=[{"name": "IBAN", "type": "iban"}])
    assert v is PanelVerdict.OK
