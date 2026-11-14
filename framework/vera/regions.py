# Autor: @Raven
"""VERA — anclaje MIXTO geométrico + textual (Fig. 1 del paper: 𝓡_S con bounding boxes).

Cuando la fuente OCR trae REGIONES (bboxes por token/línea), el anclaje combina:

  (plano)      se reconstruye el transcript en ORDEN DE LECTURA desde la geometría y se
               corre el anclaje textual probado (``vera_coverage.match_coverage``) sobre él.
  (geométrico) para los campos que el plano dejó en REVIEW/INVENTED, se usa proximidad de
               bounding boxes para desambiguar: si el valor reclamado está ESPACIALMENTE
               pegado a su etiqueta (es la región más cercana a la etiqueta) → SUPPORTED.

Sin regiones, ``engine.verify`` usa el plano puro (degradación elegante). La geometría
solo puede MEJORAR un veredicto textual (resolver colisión por repetición), nunca crear un
falso SUPPORTED que el plano no soporte por sí mismo salvo adyacencia geométrica probada.
"""
from __future__ import annotations

import re
from dataclasses import replace

from .source import Region, Source
from .structured import _canonical_equal
from .vera_coverage import CoverageRecord, CoverageReport, _blocks_ok, match_coverage
from .vera_schema import EntitySpec, SemanticType
from .vera_types import EntityVerdict

_ACCENTS = str.maketrans("áéíóúüñ", "aeiouun")

# Familias con valor numérico/tipado anclable geométricamente (enum/text → decide el plano).
_GEO_TYPES = {
    SemanticType.NUMBER, SemanticType.CURRENCY_AMOUNT, SemanticType.PERCENTAGE,
    SemanticType.DATE, SemanticType.PERIOD, SemanticType.IDENTIFIER,
}


def _toks(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (s or "").lower().translate(_ACCENTS))


def _label_match(region_text: str, labels: list[str]) -> bool:
    """La región contiene la etiqueta (o un sinónimo) como subsecuencia de tokens."""
    rt = _toks(region_text)
    for lab in labels:
        lt = _toks(lab)
        if not lt:
            continue
        n = len(lt)
        if any(rt[i:i + n] == lt for i in range(len(rt) - n + 1)):
            return True
    return False


def _center(b: tuple[float, float, float, float]) -> tuple[float, float]:
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def _dist(a: Region, b: Region) -> float:
    if a.page != b.page:
        return float("inf")
    (ax, ay), (bx, by) = _center(a.bbox), _center(b.bbox)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


# ── Transcript en orden de lectura desde la geometría ─────────────────────────

def build_transcript(regions: list[Region], line_tol: float = 6.0) -> str:
    """Ordena las regiones por (página, y, x), agrupa por línea (y similar) y las une —
    así etiqueta y valor quedan ADYACENTES en el texto, que es lo que el anclaje plano
    necesita. Páginas separadas por form-feed (``\\f``)."""
    if not regions:
        return ""
    out_pages: list[str] = []
    by_page: dict[int, list[Region]] = {}
    for r in regions:
        by_page.setdefault(r.page, []).append(r)
    for page in sorted(by_page):
        regs = sorted(by_page[page], key=lambda r: (r.bbox[1], r.bbox[0]))
        lines: list[list[Region]] = []
        for r in regs:
            if lines and abs(_center(r.bbox)[1] - _center(lines[-1][-1].bbox)[1]) <= line_tol:
                lines[-1].append(r)
            else:
                lines.append([r])
        text_lines = [
            " ".join(x.text for x in sorted(ln, key=lambda r: r.bbox[0]))
            for ln in lines
        ]
        out_pages.append("\n".join(text_lines))
    return "\f".join(out_pages)


# ── Anclaje geométrico por proximidad de bbox ─────────────────────────────────

def geometric_anchor(spec: EntitySpec, value: str, regions: list[Region]) -> EntityVerdict | None:
    """Veredicto geométrico o None (indeciso → decide el plano).

    - El valor reclamado no está en ninguna región (canónicamente) → None (INVENTED lo dice el plano).
    - Existe y es la región MÁS CERCANA a alguna etiqueta → SUPPORTED (adyacencia probada).
    - Existe pero otra región de la misma familia está más cerca de la etiqueta → REVIEW (colisión).
    - Existe una sola vez y no hay etiqueta → SUPPORTED (único, sin ambigüedad).
    """
    if spec.type not in _GEO_TYPES:
        return None
    value_regs = [r for r in regions if _canonical_equal(spec, value, r.text)]
    if not value_regs:
        return None
    labels = [spec.name, *spec.aliases]
    label_regs = [r for r in regions if _label_match(r.text, labels)]
    if not label_regs:
        return EntityVerdict.SUPPORTED if len(value_regs) == 1 else None
    # regiones de la MISMA familia con un valor DISTINTO al reclamado (candidatas a colisión)
    other_regs = [
        r for r in regions
        if r not in value_regs and _has_family_value(spec, r.text)
    ]
    d_val = min((_dist(v, lab) for v in value_regs for lab in label_regs), default=float("inf"))
    d_oth = min((_dist(o, lab) for o in other_regs for lab in label_regs), default=float("inf"))
    if d_val == float("inf"):
        return None
    return EntityVerdict.SUPPORTED if d_val <= d_oth else EntityVerdict.REVIEW


def _has_family_value(spec: EntitySpec, text: str) -> bool:
    """¿La región contiene ALGÚN valor de la familia de ``spec`` (no importa cuál)? Se usa
    para detectar candidatos a colisión. Barato: reusa el comparador canónico contra sí mismo."""
    from .vera_tribunal import parse_numeric_mentions
    if spec.type in (SemanticType.NUMBER, SemanticType.CURRENCY_AMOUNT, SemanticType.PERCENTAGE):
        return any(m.is_amount and m.magnitude is not None
                   for m in parse_numeric_mentions(text.replace("%", " ")))
    # date/period/identifier: aproximación — hay dígitos/estructura; el plano ya afina.
    return bool(re.search(r"\d", text))


# ── Cobertura híbrida ─────────────────────────────────────────────────────────

def hybrid_coverage(
    output: dict[str, str | None],
    source: Source,
    contract: list[EntitySpec],
) -> CoverageReport:
    """Cobertura MIXTA: plano sobre el transcript geométrico + refinamiento geométrico.
    La geometría solo mejora (REVIEW→SUPPORTED por adyacencia, INVENTED→REVIEW por colisión);
    nunca degrada un SUPPORTED del plano."""
    regions = source.regions or []
    transcript = build_transcript(regions) if regions else (source.text or "")
    base = match_coverage(output, transcript, contract)
    if not regions:
        return base

    new_records: list[CoverageRecord] = []
    for spec, rec in zip(contract, base.records, strict=True):
        verdict = rec.verdict
        reason = rec.reason
        if rec.llm_value is not None and verdict in (EntityVerdict.REVIEW, EntityVerdict.INVENTED):
            geo = geometric_anchor(spec, str(rec.llm_value), regions)
            if geo is EntityVerdict.SUPPORTED:
                verdict, reason = EntityVerdict.SUPPORTED, reason + " [geom: adyacente a su etiqueta]"
            elif geo is EntityVerdict.REVIEW and verdict is EntityVerdict.INVENTED:
                verdict, reason = EntityVerdict.REVIEW, reason + " [geom: colisión por proximidad]"
        new_records.append(
            rec if verdict is rec.verdict else replace(rec, verdict=verdict, reason=reason))

    ok = not any(_blocks_ok(spec, r.verdict)
                 for spec, r in zip(contract, new_records, strict=True))
    return CoverageReport(ok=ok, records=new_records, llm_output=output)
