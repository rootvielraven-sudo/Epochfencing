# Autor: @Raven
"""VERA — ruta ESTRUCTURADA (no-OCR): lookup determinista contra path/celda.

Segunda rama del predicado ``detect`` (la primera es el anclaje OCR de
``vera_coverage``). Para JSON/XML/FHIR/relacional la REGIÓN es el localizador de
esquema, CONOCIDO a priori — no hay que descubrirlo. Por cada entidad del contrato:

    valor del LLM vs. value(path)  con la MISMA canonicalización por familia
    que usa la ruta OCR (numérico/fecha/período/identificador/enum/texto):

      path ausente en la fuente            → INVENTED   (anchor = ⊥, fabricación)
      value(path) canónicamente igual      → SUPPORTED  (valid)
      value(path) presente pero distinto   → INVENTED*  (divergencia — *matched_source
                                             lleva el valor correcto de la fuente,
                                             corregible por reemplazo, §detect del paper)
      el LLM devolvió null                 → UNVERIFIED

Determinista, sin LLM. Reusa los canonicalizadores de familia de la ruta OCR: un
mismo valor en otro formato (1.234,56 ≡ 1234.56 ; 15/03/2024 ≡ 2024-03-15) ancla.
"""
from __future__ import annotations

from typing import Any

from .vera_coverage import CoverageRecord, CoverageReport, _blocks_ok
from .vera_dates import anchor_date, locale_dayfirst
from .vera_identifiers import anchor_identifier
from .vera_periods import anchor_period
from .vera_schema import EntitySpec, SemanticType
from .vera_text import anchor_enum, anchor_text
from .vera_tribunal import NumericIntegrityJudge, parse_numeric_mentions
from .vera_types import EntityVerdict

# Instancia compartida SOLO para reusar su tolerancia numérica (misma que la ruta OCR).
_NUMERIC = NumericIntegrityJudge()


# ── Resolución de la región (path/celda) ─────────────────────────────────────

def _dig(doc: Any, dotted: str) -> Any:
    """Camina un path punteado ('invoice.total') sobre dicts anidados. None si no existe."""
    cur = doc
    for part in str(dotted).split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _leaf_map_ci(doc: Any) -> dict[str, Any]:
    """Mapa case-insensitive {clave_hoja: valor} de todas las hojas escalares del dict."""
    out: dict[str, Any] = {}

    def walk(d: Any) -> None:
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, (dict, list)):
                    walk(v)
                else:
                    out.setdefault(str(k).lower(), v)
        elif isinstance(d, list):
            for it in d:
                walk(it)

    walk(doc)
    return out


def _default_resolver(doc: Any, spec: EntitySpec) -> Any:
    """Resolver por defecto (sin path_resolver del contrato): intenta path punteado por
    clave/nombre/alias, y si no, lookup por clave-hoja case-insensitive. None = ausente."""
    if doc is None:
        return None
    candidates = [spec.key, spec.name, *spec.aliases]
    for c in candidates:
        v = _dig(doc, c)
        if v is not None:
            return v
    leaves = _leaf_map_ci(doc)
    for c in candidates:
        v = leaves.get(str(c).lower())
        if v is not None:
            return v
    return None


# ── Comparación canónica por familia (mismas funciones que la ruta OCR) ───────

def _canonical_equal(spec: EntitySpec, llm_value: str, src_text: str) -> bool:
    """True si el valor del LLM ancla canónicamente contra el valor de la fuente,
    usando el extractor de la familia declarada (idéntico a la ruta OCR)."""
    etype = spec.type
    if etype in (SemanticType.NUMBER, SemanticType.CURRENCY_AMOUNT, SemanticType.PERCENTAGE):
        lv = [m.magnitude for m in parse_numeric_mentions(llm_value.replace("%", " "))
              if m.is_amount and m.magnitude is not None]
        sv = [m.magnitude for m in parse_numeric_mentions(src_text.replace("%", " "))
              if m.is_amount and m.magnitude is not None]
        return bool(lv and sv and any(_NUMERIC._matches(a, b) for a in lv for b in sv))
    if etype is SemanticType.DATE:
        return anchor_date(llm_value, src_text, locale_dayfirst(spec.locale)) is EntityVerdict.SUPPORTED
    if etype is SemanticType.PERIOD:
        return anchor_period(llm_value, src_text) is EntityVerdict.SUPPORTED
    if etype is SemanticType.IDENTIFIER:
        return anchor_identifier(llm_value, src_text, spec.fmt) is EntityVerdict.SUPPORTED
    if etype is SemanticType.ENUM:
        return anchor_enum(llm_value, src_text, spec.enum or ()) is EntityVerdict.SUPPORTED
    if etype is SemanticType.TEXT_ANCHORED:
        return anchor_text(llm_value, src_text) is EntityVerdict.SUPPORTED
    # TEXT plano: sin anclaje canónico → se resuelve como no-confirmable arriba.
    return False


def _compare(spec: EntitySpec, llm_value: str | None, src_value: Any) -> tuple[EntityVerdict, str | None, str]:
    """Predicado detect para la ruta estructurada. Devuelve (veredicto, valor_fuente, motivo)."""
    if llm_value is None:
        return EntityVerdict.UNVERIFIED, None, "el LLM devolvió null (no encontrado)"
    if src_value is None:
        return EntityVerdict.INVENTED, None, f"'{spec.name}' no existe en la fuente (anchor = ⊥)"
    src_text = str(src_value)
    if spec.type is SemanticType.TEXT:
        return EntityVerdict.UNVERIFIED, None, "tipo 'text' (prosa libre) sin anclaje canónico"
    if _canonical_equal(spec, str(llm_value), src_text):
        return EntityVerdict.SUPPORTED, src_text, "ancla contra el valor de la ruta declarada"
    # La región existe pero el valor difiere → DIVERGENCIA (corregible por reemplazo).
    return (EntityVerdict.INVENTED, src_text,
            f"divergencia: la fuente dice '{src_text}' en la ruta declarada")


def match_structured(
    output: dict[str, str | None],
    source: Any,           # framework.vera.source.Source (STRUCTURED)
    contract: list[EntitySpec],
) -> CoverageReport:
    """Verifica el output del LLM contra una fuente ESTRUCTURADA. Determinista, no bloquea.

    Reglas de ok global idénticas a la ruta OCR (``_blocks_ok``): INVENTED siempre
    bloquea; UNVERIFIED/REVIEW bloquea solo si la entidad es requerida."""
    resolver = source.path_resolver or _default_resolver
    doc = source.doc
    records: list[CoverageRecord] = []
    for spec in contract:
        llm_val = output.get(spec.key)
        src_val = resolver(doc, spec)
        verdict, matched, reason = _compare(spec, llm_val, src_val)
        records.append(CoverageRecord(
            spec.name, spec.type, llm_val, verdict, reason, matched_source=matched))
    ok = not any(_blocks_ok(spec, r.verdict)
                 for spec, r in zip(contract, records, strict=True))
    return CoverageReport(ok=ok, records=records, llm_output=output)
