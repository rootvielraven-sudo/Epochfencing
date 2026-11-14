# Autor: @Raven
"""Anclaje MIXTO geométrico + plano: la geometría refina lo que el texto deja dudoso.

Layout: a la DERECHA de "Total" (misma línea) hay un número distinto (1.000,00, p.ej. una
cantidad), y el verdadero total (2.500,00) está DEBAJO. El anclaje plano toma el número
textualmente más cercano (1.000,00) → para el total real 2.500,00 da REVIEW. La geometría
prueba que 2.500,00 está pegado (debajo) de "Total" → lo rescata a SUPPORTED.
"""
from __future__ import annotations

from framework.vera import Source, load_contract, verify
from framework.vera.regions import build_transcript
from framework.vera.source import Region

_CONTRACT = load_contract({"entities": [{"name": "Total", "type": "number"}]})

_REGIONS = [
    Region("Total",    (0, 0, 40, 10), page=1),
    Region("1.000,00", (50, 0, 110, 10), page=1),   # misma línea, a la derecha (más cercano en texto)
    Region("2.500,00", (0, 20, 60, 30), page=1),     # debajo de la etiqueta (más cercano geométrico)
]


def test_transcript_reading_order():
    lines = build_transcript(_REGIONS).split("\n")
    assert lines[0] == "Total 1.000,00"
    assert lines[1] == "2.500,00"


def test_plano_solo_da_review_para_el_total_real():
    transcript = build_transcript(_REGIONS)
    rep = verify({"Total": "2500"}, Source.ocr(transcript), _CONTRACT)
    total = rep.records[0]
    assert total.verdict.value == "review"     # 1.000,00 es el más cercano en texto


def test_geometria_rescata_el_total_real():
    rep = verify({"Total": "2500"}, Source.ocr("", regions=_REGIONS), _CONTRACT)
    total = rep.records[0]
    assert total.verdict.value == "supported"
    assert "geom" in total.reason


def test_geometria_no_inventa_valor_ausente():
    rep = verify({"Total": "9999"}, Source.ocr("", regions=_REGIONS), _CONTRACT)
    assert rep.records[0].verdict.value == "invented"   # 9999 no está en ninguna región


def test_sin_regiones_cae_a_plano():
    rep = verify({"Total": "100"}, Source.ocr("Total: 100,00\n"), _CONTRACT)
    assert rep.records[0].verdict.value == "supported"
