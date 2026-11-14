# Autor: @Raven
"""Sinónimos de etiqueta — el contrato declara aliases para la etiqueta de una entidad.

'IVA' y 'Impuesto al Valor Agregado' son la misma etiqueta. Sin aliases, si el doc usa el
sinónimo, VERA no reconoce la etiqueta → REVIEW (ruido en valores correctos). Con aliases
declarados, VERA ancla por cualquiera de los nombres → SUPPORTED. Opt-in (sin aliases, intacto).
"""
from __future__ import annotations

from framework.vera.vera_coverage import load_contract, match_coverage
from framework.vera.vera_schema import EntitySpec, SemanticType, serialize_contract
from framework.vera.vera_types import EntityVerdict

NUM = SemanticType.NUMBER
SUP, REV = EntityVerdict.SUPPORTED, EntityVerdict.REVIEW

_OCR = "Detalle. Impuesto al Valor Agregado: 210,00 EUR. Fin."


def _v(value, *, name="IVA", aliases=()):
    spec = EntitySpec(name, NUM, aliases=aliases)
    return match_coverage({spec.key: value}, _OCR, [spec]).records[0].verdict


def test_sin_alias_el_sinonimo_no_ancla() -> None:
    # el doc usa el sinónimo; sin aliases declarados → no se reconoce la etiqueta → REVIEW
    assert _v("210.00") is REV


def test_con_alias_el_sinonimo_ancla_supported() -> None:
    assert _v("210.00", aliases=("Impuesto al Valor Agregado",)) is SUP


def test_nombre_primario_sigue_anclando() -> None:
    spec = EntitySpec("Impuesto al Valor Agregado", NUM, aliases=("IVA",))
    assert match_coverage({spec.key: "210.00"}, _OCR, [spec]).records[0].verdict is SUP


def test_alias_que_no_aparece_sigue_review() -> None:
    assert _v("210.00", aliases=("Retención",)) is REV   # alias no está en el doc


def test_aliases_round_trip_contrato() -> None:
    c = [EntitySpec("IVA", NUM, aliases=("Impuesto al Valor Agregado", "I.V.A."))]
    assert load_contract(serialize_contract(c)) == c
