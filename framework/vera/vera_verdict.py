# Autor: @Raven
"""VERA — VEREDICTO UNIFICADO: compone las dos dimensiones en un solo estado.

Integración "compuesta" (no fusión de motores): cada dimensión sigue en su módulo,
pero el pipeline ve UN veredicto. Así el revisor tiene una sola respuesta ("factura
OK / revisar / bloqueada") en vez de dos señales sueltas.

Dimensiones:
  - COBERTURA (``vera_coverage``/``integration``): ¿cada entidad ancla contra el OCR?
    Señal: bloquea si hay alucinación dura (INVENTED); revisa si hay hallazgos.
  - CÁLCULO (``calc_consistency``): ¿cierran las cuentas? Señal: bloquea si hay
    inconsistencia DEMOSTRADA; revisa si quedó INCOMPLETE (no verificable).
  - CONTRADICCIÓN (``vera_tribunal``): ¿alguna CIFRA del output choca con una cifra
    relacionada de la fuente (misma cifra corrompida, moneda/escala cambiada, aritmética
    interna rota)? Opt-in (``VERA_CONTRADICTION``, default OFF). Señal: bloquea SOLO si un
    juez DURO marcó ``CONTRADICTED`` con evidencia real; si no, SE ABSTIENE (no certifica
    limpio). Con el flag OFF no participa y el veredicto es idéntico al histórico.

Estado combinado (el más severo gana): ``blocked`` > ``review`` > ``clean``. Si una
dimensión no corrió (flag off / sin evidencia), simplemente no aporta señal.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

# Severidad para elegir el estado combinado.
_RANK = {"clean": 0, "review": 1, "blocked": 2}
_STATE = {0: "clean", 1: "review", 2: "blocked"}


def coverage_state(blocked: bool, has_findings: bool) -> str:
    """Estado de la dimensión de cobertura."""
    if blocked:
        return "blocked"
    return "review" if has_findings else "clean"


def calc_state(calc_result_state: Optional[str]) -> Optional[str]:
    """Estado de la dimensión de cálculo a partir de ``CalcState.value``.

    FAIL-CLOSED (regla de oro): un verificador que FALLÓ nunca puede certificar
    ``clean``. Se distinguen EXPLÍCITAMENTE dos casos antes confundidos:

    - ``None``   → la dimensión NO corrió (VERA_CALC apagado) ⇒ devuelve ``None``:
      no aporta señal (``overall_state`` la trata como ausente, neutral).
    - ``"error"``→ la dimensión corrió pero FALLÓ al formalizar/ejecutar ⇒ ``review``
      (fail-closed): no se puede concluir que las cuentas cierran, pero tampoco hay
      inconsistencia demostrada que justifique ``blocked``.

    Mapeo del resto: ``inconsistent`` → ``blocked``; ``incomplete`` → ``review``;
    ``coherent`` → ``clean``. Cualquier estado desconocido de un verificador que SÍ
    corrió también cae a ``review`` (fail-closed), nunca a ``clean``."""
    if calc_result_state is None:
        return None  # la dimensión no corrió: sin señal (≠ corrió y dio limpio)
    if calc_result_state == "inconsistent":
        return "blocked"
    if calc_result_state in ("incomplete", "error"):
        return "review"
    if calc_result_state == "coherent":
        return "clean"
    # Verificador que corrió con un estado no reconocido ⇒ no certifica limpio.
    return "review"


def contradiction_enabled() -> bool:
    """True si la dimensión de CONTRADICCIÓN del tribunal de cifras participa en el
    veredicto combinado. Opt-in por env ``VERA_CONTRADICTION=on`` (acepta on/1/true).

    Default OFF → inerte: ``combine`` ignora por completo la dimensión de contradicción y
    el veredicto combinado es IDÉNTICO al histórico (cobertura+cálculo). MISMO patrón que
    ``VERA_CALC`` (``framework/vera/calc_contract.py:calc_enabled``)."""
    return os.getenv("VERA_CONTRADICTION", "off").strip().lower() in ("on", "1", "true")


def contradiction_state(tribunal_result: Any) -> Optional[str]:
    """Estado de la dimensión de CONTRADICCIÓN a partir de un ``TribunalResult``
    (``framework.vera.vera_tribunal``). CONSERVADOR y HONESTO:

    - Participa (``"blocked"``) SOLO si un juez DURO del tribunal marcó al menos una cifra
      ``CONTRADICTED`` con evidencia real (misma cifra corrompida fuera de la tolerancia
      de redondeo pero dentro de la banda relativa del 20%, cambio de moneda/escala, o
      relación aritmética interna que no cierra). Una contradicción DEMOSTRADA no puede
      certificar "aceptar/coherente" → degrada a ``blocked`` (fail-closed), alineado con
      cómo ``calc`` INCONSISTENT degrada hoy.
    - En cualquier otro caso —no hay cifra ``CONTRADICTED``, o no hubo evidencia de cifra
      fuente que comparar— la dimensión SE ABSTIENE (``None``): NO aporta señal y NO
      certifica limpio (eso lo deciden cobertura/cálculo). Nunca inventa una contradicción
      ni bloquea de más.

    Solo cuenta ``CONTRADICTED`` (no ``NOT_FOUND``/``AMBIGUOUS``): esos ya los cubren la
    cobertura (invento/ausencia) y la revisión, no esta dimensión."""
    if tribunal_result is None:
        return None
    # ``FigureStatus`` es StrEnum: ``status == "contradicted"`` vale tanto si es el enum
    # como si ya es string → no requiere importar vera_tribunal (evita acople/circulares).
    evidence = getattr(tribunal_result, "blocking_evidence", None) or []
    has_contradiction = any(
        getattr(f, "status", None) == "contradicted" for f in evidence
    )
    return "blocked" if has_contradiction else None


def overall_state(
    *,
    coverage: Optional[str] = None,
    calc: Optional[str] = None,
    contradiction: Optional[str] = None,
) -> str:
    """Combina los estados por-dimensión en uno solo (el más severo). ``None`` = dimensión
    ausente (no aporta)."""
    rank = 0
    for s in (coverage, calc, contradiction):
        if s is not None:
            rank = max(rank, _RANK.get(s, 0))
    return _STATE[rank]


def combine(
    *,
    coverage: Optional[str] = None,
    calc: Optional[str] = None,
    calc_diagnosis: Optional[Dict[str, Any]] = None,
    contradiction: Optional[str] = None,
) -> Dict[str, Any]:
    """Veredicto VERA unificado (JSON-serializable) para adjuntar al output.

    ``state`` es el combinado; ``dimensions`` detalla cada una; ``blocked`` es el gate
    (regla de oro: una alucinación dura, una inconsistencia demostrada o una contradicción
    de cifra demostrada no pasan).

    Dimensión de CONTRADICCIÓN (opt-in ``VERA_CONTRADICTION``): con el flag OFF se IGNORA
    por completo —el argumento ``contradiction`` no altera nada y el resultado es idéntico
    al histórico cobertura+cálculo—. Con el flag ON participa como una dimensión más (el
    más severo gana) y solo aparece en ``dimensions`` cuando aporta señal (no abstiene),
    de modo que el caso "flag ON sin contradicción" produce exactamente el mismo dict que
    siempre."""
    # Gate del flag dentro de combine → fail-safe: aunque un caller pase ``contradiction``
    # con el flag apagado, el comportamiento queda idéntico al actual.
    contra = contradiction if contradiction_enabled() else None
    state = overall_state(coverage=coverage, calc=calc, contradiction=contra)
    out: Dict[str, Any] = {
        "state": state,
        "blocked": state == "blocked",
        "dimensions": {"coverage": coverage, "calc": calc},
    }
    if contra is not None:
        out["dimensions"]["contradiction"] = contra
    if calc_diagnosis is not None and calc == "blocked":
        out["calc_diagnosis"] = calc_diagnosis
    return out


__all__ = [
    "coverage_state", "calc_state", "overall_state", "combine",
    "contradiction_enabled", "contradiction_state",
]
