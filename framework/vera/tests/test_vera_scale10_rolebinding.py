# Autor: @Raven
"""Resolución de scale10 con ROLE-BINDING aritmético (brief HADD).

scale10 FN: corromper ×10 una cifra produce una magnitud que YA existe en la fuente (p.ej.
IVA×10 = base, o costas×10 = principal). NumericIntegrity (anclaje por existencia) la da por
buena → FN. ArithmeticCoherence corría SOLO sobre la fuente (consistente) → no la atrapaba.

Fix: la aritmética debe cerrar también en el OUTPUT. Si el output corrompió un componente de
una relación (total = base + iva), la relación NO cierra en el output → CONTRADICTED.

Invariante sagrado FP=0: un re-render fiel (otro formato, misma aritmética) NO debe disparar.
"""
from __future__ import annotations

from framework.vera.vera_tribunal import ArithmeticCoherenceJudge

# fuente consistente: 110.000 = 100.000 + 10.000
_SRC = "Factura. Base imponible: 100.000,00. IVA: 10.000,00. Total a pagar: 110.000,00."
# output con el IVA corrompido ×10 = 100.000 (coincide con la base → existe en la fuente)
_OUT_SCALE10 = "Factura. Base imponible: 100.000,00. IVA: 100.000,00. Total a pagar: 110.000,00."


def test_scale10_relacional_se_detecta_en_el_output() -> None:
    # la aritmética del OUTPUT no cierra (100k + 100k ≠ 110k) → CONTRADICTED (juez duro)
    v = ArithmeticCoherenceJudge().judge(_OUT_SCALE10, _SRC)
    assert v.has_error, "scale10: el componente corrompido rompe la relación en el output"


def test_no_FP_en_rerender_fiel() -> None:
    # INVARIANTE FP=0: el output reproduce fielmente (otro formato), la aritmética cierra → sin finding
    out_clean = "FACTURA — Base imponible 100000,00 | IVA 10000,00 | Total a pagar 110000,00"
    v = ArithmeticCoherenceJudge().judge(out_clean, _SRC)
    assert not v.has_error


def test_scale10_embargo_costas_se_detecta() -> None:
    # el ejemplo EXACTO del brief: costas = 10% del principal; corromper costas ×10 = principal
    # (existe en la fuente → NumericIntegrity la pasa) → la relación total=principal+costas no
    # cierra en el output → CONTRADICTED.
    src = ("Embargo. Capital principal: 1.000.000,00. Costas (10%): 100.000,00. "
           "Total a pagar: 1.100.000,00.")
    out = ("Embargo. Capital principal: 1.000.000,00. Costas (10%): 1.000.000,00. "
           "Total a pagar: 1.100.000,00.")   # costas ×10 = principal
    assert ArithmeticCoherenceJudge().judge(out, src).has_error


def test_no_FP_cuando_no_hay_relacion_completa() -> None:
    # documento sin las etiquetas de ninguna relación → el juez no dispara (conservador)
    src = "Recibo. Importe abonado: 5.000,00. Referencia: 12345."
    out = "Recibo. Importe abonado: 50.000,00. Referencia: 12345."   # ×10 pero sin relación
    v = ArithmeticCoherenceJudge().judge(out, src)
    assert not v.has_error   # no hay relación que evaluar → REVIEW/otros jueces, no este
