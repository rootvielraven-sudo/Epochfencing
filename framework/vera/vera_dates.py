# Autor: @Raven
"""VERA — extractor/anclaje CANÓNICO de fechas (ADR-022 paso 3). Determinista, sin LLM.

Canonicaliza una fecha SOLO para reconocer el MISMO valor (no interpreta calendario):
    dd/mm/aaaa  ≡  aaaa-mm-dd  ≡  "15 de marzo de 2024"   → (año, mes, día)

Ancla output→OCR: si la fecha del LLM canoniza a una fecha del OCR → SUPPORTED; si no
está → INVENTED; si la fecha es GENUINAMENTE ambigua (dd/mm vs mm/dd, ambos ≤12) y no se
puede resolver contra el OCR → REVIEW (NUNCA una convención hardcodeada que invente certeza).

Corrige de paso la asimetría: con extractor de fecha en ambos lados, el día de una fecha
textual ("15 de marzo") deja de colarse como monto suelto en NumericIntegrity.
"""
from __future__ import annotations

import re

from .vera_types import EntityVerdict

# Canónico de una fecha: (año, mes, día). Un token puede dar 1 (inequívoco) o 2 (ambiguo).
_Canon = tuple[int, int, int]
_CanonSet = frozenset[_Canon]

_MONTHS: dict[str, int] = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_NUM_RE = re.compile(r"\b(\d{1,2})[/-](\d{1,2})[/-](\d{4})\b")
_TXT_RE = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?(" + "|".join(_MONTHS) + r")\s+(?:de\s+)?(\d{4})\b",
    re.IGNORECASE,
)


def _valid(y: int, m: int, d: int) -> bool:
    return 1 <= m <= 12 and 1 <= d <= 31


# Locales que leen mm/dd (mes primero). El resto → dd/mm (día primero). None → no desambigua.
_MONTH_FIRST = {"en-us", "en_us", "us", "en-ph", "en_ph"}


def locale_dayfirst(locale: str | None) -> bool | None:
    """Resuelve el orden de fecha del locale: True=dd/mm, False=mm/dd, None=sin desambiguar."""
    if not locale:
        return None
    return locale.strip().lower() not in _MONTH_FIRST   # month-first → False, resto → True (dd/mm)


def parse_dates(text: str, dayfirst: bool | None = None) -> list[_CanonSet]:
    """Extrae las fechas de un texto como conjuntos canónicos de candidatos (y,m,d).

    Inequívoca → conjunto de 1. Ambigua (numérica con ambos componentes ≤12) → conjunto
    de 2, SALVO que ``dayfirst`` esté seteado (locale declarado) → resuelve a 1 candidato.
    """
    out: list[_CanonSet] = []
    seen_spans: list[tuple[int, int]] = []

    def _mark(m: re.Match[str]) -> bool:
        return any(s < m.end() and m.start() < e for s, e in seen_spans)

    for m in _ISO_RE.finditer(text or ""):
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
        if _valid(y, mo, d):
            out.append(frozenset({(y, mo, d)}))
            seen_spans.append(m.span())

    for m in _TXT_RE.finditer(text or ""):
        d, mo, y = int(m[1]), _MONTHS[m[2].lower()], int(m[3])
        if _valid(y, mo, d):
            out.append(frozenset({(y, mo, d)}))
            seen_spans.append(m.span())

    for m in _NUM_RE.finditer(text or ""):
        if _mark(m):                       # ya capturada como ISO/textual
            continue
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        dd_mm = (y, b, a) if _valid(y, b, a) else None    # día=a, mes=b
        mm_dd = (y, a, b) if _valid(y, a, b) else None    # mes=a, día=b
        if dayfirst is True:
            chosen = dd_mm or mm_dd
        elif dayfirst is False:
            chosen = mm_dd or dd_mm
        else:
            chosen = None                  # sin locale → conserva la ambigüedad
        if chosen is not None:
            out.append(frozenset({chosen}))
        else:
            cands = {c for c in (dd_mm, mm_dd) if c is not None}
            if cands:
                out.append(frozenset(cands))
    return out


def anchor_date(llm_value: str, ocr: str, dayfirst: bool | None = None) -> EntityVerdict:
    """Veredicto de cobertura para una entidad de tipo DATE. Determinista.

    SUPPORTED : la fecha del LLM canoniza a una fecha presente en el OCR (mismo valor,
                cualquier formato). Con ``dayfirst`` (locale declarado) las fechas ambiguas
                dd/mm↔mm/dd se resuelven y pueden anclar SUPPORTED.
    REVIEW    : hay solape pero ambos lados quedan ambiguos (no se puede afirmar mismo valor).
    INVENTED  : la fecha del LLM no está en el OCR (en ninguna forma).
    (null → UNVERIFIED lo decide match_coverage antes de llamar acá.)
    """
    v_dates = parse_dates(llm_value, dayfirst)
    if not v_dates:
        return EntityVerdict.REVIEW          # no parseable como fecha → no afirmamos INVENTED
    ocr_dates = parse_dates(ocr, dayfirst)
    vc = v_dates[0]

    # SUPPORTED solo con confirmación INEQUÍVOCA en AMBOS lados (ambos resuelven a la MISMA
    # única fecha). Si hay solape de candidatos pero algún lado queda ambiguo → REVIEW
    # (no afirmamos mismo valor; podría ser un swap dd/mm↔mm/dd genuino). Sin solape → INVENTED.
    overlap = False
    for o in ocr_dates:
        if len(vc) == 1 and len(o) == 1 and vc == o:
            return EntityVerdict.SUPPORTED
        if vc & o:
            overlap = True
    if overlap:
        return EntityVerdict.REVIEW
    # Tolerancia OCR NO-DESTRUCTIVA: ¿la fecha aparece si se pliegan confusiones en tokens
    # numéricos (p.ej. "12/O3/2024" → "12/03/2024")? clean_ocr_text solo toca tokens con dígitos
    # (no prosa) y aquí solo se usa como clave de comparación (fuente y valor quedan crudos).
    # Si aparece así → REVIEW (recuperada, flagueada, no INVENTED, no falso SUPPORTED).
    from .clean import clean_ocr_text
    if any(vc & o for o in parse_dates(clean_ocr_text(ocr), dayfirst)):
        return EntityVerdict.REVIEW
    return EntityVerdict.INVENTED
