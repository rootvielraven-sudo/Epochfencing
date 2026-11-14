# Autor: @Raven
"""El símbolo '$' por defecto → ARS (peso), con disambiguación de dólares argentinos.

En contexto AR (default de MINERVA) '$' = pesos. Pero 'US$' / 'U$S' / 'USD' / 'dólar' = USD y NO
deben leerse como pesos. Las divisas con símbolo propio (€/£/¥) y los códigos ISO (USD/EUR/…) no
cambian.
"""
from __future__ import annotations

from framework.vera.vera_tribunal import normalize_currency, parse_numeric_mentions


def _cur(text: str) -> str:
    ms = [m for m in parse_numeric_mentions(text) if m.is_amount]
    return ms[0].currency if ms else ""


def test_peso_simbolo_pelado_es_ars() -> None:
    assert _cur("Total: $1.000,00") == "ARS"
    assert _cur("Total: $ 1.000.000,00") == "ARS"


def test_dolar_argentino_us_es_usd() -> None:
    assert _cur("Total: US$ 1.000,00") == "USD"
    assert _cur("Total: U$S 1.000,00") == "USD"


def test_codigo_iso_explicito_intacto() -> None:
    assert _cur("Total: 1.000,00 USD") == "USD"
    assert _cur("Total: 1.000,00 ARS") == "ARS"


def test_otros_simbolos_intactos() -> None:
    assert _cur("Total: €1.000,00") == "EUR"
    assert _cur("Total: £1.000,00") == "GBP"


def test_normalize_currency_simbolo_peso() -> None:
    assert normalize_currency("$") == "ARS"   # el contrato puede declarar '$' → ARS


def test_dollar_default_configurable_por_region() -> None:
    from framework.vera.vera_tribunal import set_dollar_default
    try:
        set_dollar_default("USD")                       # deployment US/internacional
        assert _cur("Total: $1.000,00") == "USD"
        assert normalize_currency("$") == "USD"
        assert _cur("Total: US$ 1.000,00") == "USD"     # US$ sigue USD (override)
        set_dollar_default("EUR")                        # deployment ES
        assert _cur("Total: $1.000,00") == "EUR"
    finally:
        set_dollar_default("ARS")                        # restaurar default
    assert _cur("Total: $1.000,00") == "ARS"             # default AR
