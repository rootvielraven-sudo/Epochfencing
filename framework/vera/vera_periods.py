# Autor: @Raven
"""VERA — anclaje canónico de PERÍODOS (P1). Determinista, sin LLM.

Un PERÍODO es mes-año, trimestre o año (extractos, nóminas, facturas):
    03/2024 ≡ 2024-03 ≡ "marzo 2024" ≡ "marzo de 2024"   → ("M", 2024, 3)
    Q1 2024 ≡ T1 2024 ≡ 2024-Q1                          → ("Q", 2024, 1)
    2024                                                  → ("Y", 2024)

Ancla output→OCR por EXISTENCIA del período canónico. NO confunde con una fecha-DÍA: primero
marca los spans de fechas completas (dd/mm/aaaa, ISO, "15 de marzo de 2024") y NO extrae períodos
de adentro de ellas — "01/04/2024" es una fecha, no el período "abril 2024".

    presente como período → SUPPORTED   ·   no parseable → REVIEW   ·   ausente → INVENTED
(rol/proximidad y null los maneja match_coverage; acá es existencia en el texto dado.)
"""
from __future__ import annotations

import re

from .vera_dates import _ISO_RE, _MONTHS, _NUM_RE, _TXT_RE
from .vera_types import EntityVerdict

_Period = tuple[str, int, int] | tuple[str, int]

_MY_NUM = re.compile(r"\b(\d{1,2})[/-](\d{4})\b")                    # mm/aaaa
_ISO_MY = re.compile(r"\b(\d{4})-(\d{1,2})\b")                      # aaaa-mm
_MY_TXT = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(?:de\s+)?(\d{4})\b", re.IGNORECASE)
_Q = re.compile(r"\b[QT]\s*([1-4])\s*(?:de\s+)?[-/ ]?\s*(\d{4})\b", re.IGNORECASE)
_Q_REV = re.compile(r"\b(\d{4})\s*[-/ ]?\s*[QT]\s*([1-4])\b", re.IGNORECASE)
_YEAR = re.compile(r"\b(20\d{2})\b")


def parse_periods(text: str) -> list[_Period]:
    """Extrae los períodos canónicos del texto. Marca primero las FECHAS-día y no extrae
    períodos de adentro de ellas (evita leer 'abril 2024' dentro de '01/04/2024')."""
    text = text or ""
    out: list[_Period] = []
    spans: list[tuple[int, int]] = []

    def overlaps(m: re.Match[str]) -> bool:
        return any(s < m.end() and m.start() < e for s, e in spans)

    def take(m: re.Match[str], period: _Period) -> None:
        out.append(period)
        spans.append(m.span())

    # 1) fechas-día completas → marcar spans (no son períodos, pero bloquean sub-matches)
    for rx in (_ISO_RE, _NUM_RE, _TXT_RE):
        for m in rx.finditer(text):
            spans.append(m.span())

    # 2) mes-año (numérico / ISO / textual) y trimestre, fuera de spans de fecha
    for m in _MY_NUM.finditer(text):
        mo, y = int(m[1]), int(m[2])
        if 1 <= mo <= 12 and not overlaps(m):
            take(m, ("M", y, mo))
    for m in _ISO_MY.finditer(text):
        y, mo = int(m[1]), int(m[2])
        if 1 <= mo <= 12 and not overlaps(m):
            take(m, ("M", y, mo))
    for m in _MY_TXT.finditer(text):
        if not overlaps(m):
            take(m, ("M", int(m[2]), _MONTHS[m[1].lower()]))
    for rx, order in ((_Q, "qy"), (_Q_REV, "yq")):
        for m in rx.finditer(text):
            if overlaps(m):
                continue
            q, y = (int(m[1]), int(m[2])) if order == "qy" else (int(m[2]), int(m[1]))
            take(m, ("Q", y, q))

    # 3) año suelto (fallback) fuera de todo span ya tomado
    for m in _YEAR.finditer(text):
        if not overlaps(m):
            take(m, ("Y", int(m[1])))
    return out


def anchor_period(llm_value: str, ocr: str) -> EntityVerdict:
    """Veredicto de cobertura para una entidad PERIOD. Determinista.

    SUPPORTED si el período canónico del LLM existe en el texto dado; INVENTED si no; REVIEW si
    el valor no parsea como período. (rol/proximidad → match_coverage vía _anchor_by_proximity.)
    """
    v = parse_periods(llm_value)
    if not v:
        return EntityVerdict.REVIEW          # no parseable como período → no afirmamos
    return EntityVerdict.SUPPORTED if v[0] in parse_periods(ocr) else EntityVerdict.INVENTED
