# Autor: @Raven
"""Compuerta VERA: decide si una factura reconstruida entra al PDF VERIFIED o DEGRADED.

Dos dimensiones de VERA, una por cada fallo de contenido:

  - COBERTURA (``verify`` + ``map_extraction``): ¿cada valor ancla en el OCR?
    Un valor sin anclaje → veredicto ``INVENTED`` → *alucinación*.

  - CÁLCULO (``verify_calc``): ¿se cumplen los invariantes aritméticos
    (línea, subtotal, IVA, total)? Un número que los rompe → ``INCONSISTENT`` →
    *corrupción silenciosa* (el valor puede "existir" pero no cuadra).

Una factura que falla cualquiera de las dos NO bloquea la corrida: entra al PDF con su OCR
crudo y marcada (``DEGRADED``). ``enabled=False`` apaga VERA (baseline: todo VERIFIED).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from framework.vera import Source, verify
from framework.vera.calc_contract import verify_calc
from framework.vera.contract import load_contract_for
from framework.vera.integration import map_extraction
from framework.vera.vera_types import EntityVerdict
from .reconstructor import UNRECOVERABLE


def _has_unrecoverable(extraction: dict) -> bool:
    """¿El reconstructor declinó honestamente algún campo con el sentinel [UNRECOVERABLE]?"""
    for v in extraction.values():
        if isinstance(v, str) and v.strip() == UNRECOVERABLE:
            return True
    return False


@dataclass
class GateResult:
    state: str                       # "verified" | "degraded"
    reasons: list[str] = field(default_factory=list)   # ["hallucination", "unverified", "calc"]
    invented: list[str] = field(default_factory=list)  # entidades alucinadas (INVENTED)
    review: list[str] = field(default_factory=list)    # entidades REVIEW (no confirmables / rol)
    unverified: list[str] = field(default_factory=list)  # entidades UNVERIFIED (null / sin extractor)
    calc_violations: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.state == "verified"


class VeraGate:
    def __init__(self, *, doc_type: str = "factura", client: str = "default",
                 config_root: str = "config", enabled: bool = True,
                 check_coverage: bool = True, check_calc: bool = True,
                 strict: bool = False, ndl=None, learn: bool = True):
        self.enabled = enabled
        self.check_coverage = check_coverage
        self.check_calc = check_calc
        # strict=True → REVIEW/UNVERIFIED en campo requerido tampoco pasa como VERIFIED
        # (honestidad epistémica, sección 5). strict=False = conducta histórica (baseline:
        # solo INVENTED bloquea la cobertura).
        self.strict = strict
        self.doc_type = doc_type
        self.client = client
        self.config_root = config_root
        self.ndl = ndl                 # NDL opcional (memoria de anti-patrones, p.ej. en Redis)
        self.learn = learn             # si True, registra las alucinaciones detectadas
        self._contract = load_contract_for(doc_type, client=client, config_root=config_root)

    def evaluate(self, extraction: dict[str, Any], ocr_text: str) -> GateResult:
        # Declinación honesta del reconstructor (precondición violada, Prop. 1): entra
        # DEGRADED como dato faltante, sin culpar a VERA. Se chequea aun con VERA apagado.
        if _has_unrecoverable(extraction):
            return GateResult(state="degraded", reasons=["unrecoverable"])

        if not self.enabled:
            return GateResult(state="verified", reasons=[])

        reasons: list[str] = []
        invented: list[str] = []
        review: list[str] = []
        unverified: list[str] = []
        calc_viol: list[str] = []
        counts: dict[str, int] = {}

        # ── Dimensión COBERTURA → alucinación / no-confirmable ───────────────
        if self.check_coverage and self._contract:
            mapped = map_extraction(extraction, self._contract)
            rep = verify(mapped, Source.ocr(ocr_text), self._contract)
            # NDL: memoria diacrónica de anti-patrones (refuerza SUPPORTED→REVIEW si el
            # contexto coincide con alucinaciones conocidas). Persistente si el store es Redis.
            if self.ndl is not None:
                from framework.vera.ndl import apply_ndl
                rep = apply_ndl(rep, self.ndl, self.doc_type, ocr_text, contract=self._contract)
            counts = rep.counts()
            invented = [r.entity_name for r in rep.records
                        if r.verdict is EntityVerdict.INVENTED]
            review = [r.entity_name for r in rep.records
                      if r.verdict is EntityVerdict.REVIEW]
            unverified = [r.entity_name for r in rep.records
                          if r.verdict is EntityVerdict.UNVERIFIED]
            if invented:
                reasons.append("hallucination")
                # Aprende: registra cada alucinación como anti-patrón (se reusa en la próxima corrida).
                if self.ndl is not None and self.learn:
                    for r in rep.records:
                        if r.verdict is EntityVerdict.INVENTED:
                            self.ndl.record(self.doc_type, r.entity_name, ocr_text,
                                            hallucinated=str(r.llm_value),
                                            corrected=r.matched_source)
            # Honestidad epistémica (sección 5): REVIEW/UNVERIFIED en un campo REQUERIDO NO es
            # evidencia positiva → no puede pasar como VERIFIED. ``rep.ok`` ya codifica esto
            # (bloquea INVENTED siempre; REVIEW/UNVERIFIED solo si la entidad es requerida).
            elif self.strict and not rep.ok:
                reasons.append("unverified")

        # ── Dimensión CÁLCULO → corrupción silenciosa ────────────────────────
        if self.check_calc:
            cr = verify_calc(extraction, doc_type=self.doc_type, client=self.client,
                             config_root=self.config_root)
            if cr is not None and cr.demonstrated_inconsistent:
                calc_viol = [v.rule.split(":")[0] for v in cr.violations]
                reasons.append("calc")

        state = "degraded" if reasons else "verified"
        return GateResult(state=state, reasons=reasons, invented=invented,
                          review=review, unverified=unverified,
                          calc_violations=calc_viol, counts=counts)
