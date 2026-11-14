# Autor: @Raven
"""VERA — entrypoint PLANO (MIKA port, sin holones).

Reemplaza la orquestación por holones de Minerva (``vera.py`` GroundingHolon /
SemanticReactive) por una función directa que se cablea en el pipeline de MIKA.

    verify(llm_output, source, contract) → CoverageReport

Despacha por el tipo de fuente (el predicado ``detect`` del paper, dos rutas):
  - Source.ocr(...)        → anclaje por proximidad de etiqueta sobre el OCR
                             (``vera_coverage.match_coverage``).
  - Source.structured(...) → lookup determinista contra path/celda
                             (``structured.match_structured``).

Ambas devuelven el mismo ``CoverageReport`` (SUPPORTED/INVENTED/REVIEW/UNVERIFIED),
así el cableado aguas abajo (aviso al usuario, EVR buffer, flags de compliance)
es idéntico. Determinista, sin LLM en la verificación.
"""
from __future__ import annotations

from .source import Source, SourceKind
from .structured import match_structured
from .vera_coverage import CoverageReport, match_coverage
from .vera_schema import EntitySpec, ValidationResult, validate_output


def verify(
    llm_output: dict[str, str | None],
    source: Source,
    contract: list[EntitySpec],
    *,
    revalidate: bool = False,
) -> CoverageReport:
    """Verifica el output tipado del LLM contra la fuente, según el contrato.

    Args:
        llm_output: dict {clave_slug: valor|null} ya extraído por el LLM.
        source:     Source.ocr(texto) o Source.structured(doc).
        contract:   lista de EntitySpec (familias por entidad). Ver load_contract.
        revalidate: si True, revalida la FORMA del output (Fase 0) antes de anclar;
                    los errores de forma quedan disponibles vía ``check_form``.

    Returns:
        CoverageReport (ok global + veredicto por entidad).
    """
    if revalidate:
        # Cinturón y tirantes: la forma se puede chequear aparte con check_form();
        # el anclaje es robusto a claves faltantes (usa .get), así que no se aborta acá.
        _ = validate_output(llm_output, contract)

    if source.kind is SourceKind.OCR_TEXT:
        if source.regions:
            # Anclaje MIXTO geométrico + plano (Fig. 1 del paper: 𝓡_S con bboxes).
            from .regions import hybrid_coverage
            return hybrid_coverage(llm_output, source, contract)
        text = source.text or ""
        if source.clean:
            from .clean import clean_ocr_text
            text = clean_ocr_text(text)
        return match_coverage(llm_output, text, contract)
    return match_structured(llm_output, source, contract)


def check_form(llm_output: dict, contract: list[EntitySpec]) -> ValidationResult:
    """Revalidación determinista de FORMA (Fase 0): claves completas, valores string|null,
    sin claves extra. Independiente del anclaje."""
    return validate_output(llm_output, contract)
