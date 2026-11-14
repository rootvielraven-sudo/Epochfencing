# Autor: @Raven
"""VERA — capa de COBERTURA (ADR-022 punto 2). PRIMER cambio que altera comportamiento
a propósito: con contrato declarado, verifica CADA entidad del contrato contra el OCR y
emite veredicto por entidad, incluido UNVERIFIED (hasta ahora reservado en el enum).

Métrica determinista, por entidad, sobre el valor que el LLM devolvió (output ya
revalidado por la Fase 0: claves completas, valores string|null):
    valor EXISTE en OCR (anclaje CANÓNICO)  → SUPPORTED
    valor NO existe en OCR                  → INVENTED
    LLM devolvió null                       → UNVERIFIED

"Existe" = anclaje CANÓNICO (normalizado), no literal. La normalización es la MISMA
función a ambos lados (LLM y OCR): se reusa la canonicalización numérica de
``NumericIntegrityJudge`` (``parse_numeric_mentions`` + ``_matches``). No se escribe una
nueva → un mismo valor en otro formato (1.234,56 ≡ 1234.56) ancla, sin falso INVENTED.

OK global: ninguna entidad INVENTED (alucinación siempre bloquea) ∧ toda entidad REQUERIDA
SUPPORTED; una `optional` no-confirmable (UNVERIFIED/REVIEW) no bloquea. NO bloquea (notifica).

Tipos anclados: NUMBER, CURRENCY_AMOUNT (magnitud + divisa), DATE (con locale opcional),
PERIOD (mes-año/trimestre/año), IDENTIFIER (frontera de token + checksum por fmt), TEXT_ANCHORED
(presencia verbatim de tokens). TEXT plano → UNVERIFIED. Cada tipo decide su canonicalización; el
LLM nunca ve el tipo. Params de contrato (solo VERA): fmt/currency/locale/page/optional.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from .vera_dates import anchor_date, locale_dayfirst
from .vera_identifiers import KNOWN_FORMATS as _KNOWN_FORMATS
from .vera_identifiers import anchor_identifier
from .vera_periods import anchor_period
from .vera_schema import EntitySpec, SemanticType
from .vera_text import anchor_enum, anchor_text
from .vera_tribunal import (  # núcleo de canonicalización numérica (utilidad compartida)
    _NUM_TOKEN_RE,
    NumericIntegrityJudge,
    _classify_and_scale,
    normalize_currency,
    parse_numeric_mentions,
)
from .vera_types import EntityVerdict


@dataclass(frozen=True)
class CoverageRecord:
    """Veredicto por entidad, con detalle accionable (cuál y por qué)."""
    entity_name: str
    entity_type: SemanticType
    llm_value:   str | None
    verdict:     EntityVerdict
    reason:      str
    matched_source: str | None = None   # valor del OCR con el que ancló (si SUPPORTED)
    evidence:    str | None = None       # fragmento del OCR que justifica el veredicto (traza)


@dataclass(frozen=True)
class CoverageReport:
    """Resultado de cobertura: OK global + detalle por entidad. NO bloquea (notifica)."""
    ok:      bool
    records: list[CoverageRecord] = field(default_factory=list)
    llm_output: dict[str, str | None] | None = None   # output crudo del LLM (para replay/auditoría)

    @property
    def notify(self) -> list[CoverageRecord]:
        """Entidades que requieren atención (no SUPPORTED), para el aviso al usuario."""
        return [r for r in self.records if r.verdict is not EntityVerdict.SUPPORTED]

    def counts(self) -> dict[str, int]:
        c = {EntityVerdict.SUPPORTED: 0, EntityVerdict.INVENTED: 0, EntityVerdict.UNVERIFIED: 0}
        for r in self.records:
            c[r.verdict] = c.get(r.verdict, 0) + 1
        return {k.value: v for k, v in c.items()}


# Instancia compartida solo para REUSAR su canonicalización/tolerancia (no se escribe otra).
_NUMERIC = NumericIntegrityJudge()

# Ventana de PROXIMIDAD: el valor del campo va DESPUÉS de la etiqueta. _WIN_BEFORE=0
# evita capturar el valor de la línea anterior (que con líderes punteados quedaría más
# cerca de la etiqueta que el valor real). Captura "Total a pagar ...... 3.025.000,00".
_WIN_BEFORE, _WIN_AFTER = 0, 120
# Deacentuación 1:1 (preserva posiciones para mapear al OCR original).
_ACCENTS = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")


def _norm_alnum(s: str) -> str:
    """Deacentúa, baja a minúsculas y deja solo alfanuméricos. 'I.V.A.' → 'iva'."""
    return re.sub(r"[^a-z0-9]", "", s.translate(_ACCENTS).lower())


def _tokenize(text: str) -> list[tuple[str, int, int]]:
    """Tokens (norm, inicio, fin) por chunk separado por espacios, normalizado a alfanum.
    'I.V.A.' es un chunk → token 'iva'; '(base' → 'base'. Las posiciones son del texto ORIGINAL."""
    out: list[tuple[str, int, int]] = []
    for m in re.finditer(r"\S+", text):
        norm = _norm_alnum(m.group(0))
        if norm:
            out.append((norm, m.start(), m.end()))
    return out


def _ocr_magnitudes(ocr: str) -> list[Decimal]:
    return [m.magnitude for m in parse_numeric_mentions(ocr)
            if m.is_amount and m.magnitude is not None]


def _label_occurrences(ocr: str, label: str | list[str]) -> list[tuple[str, int, int]]:
    """Por cada aparición de la ETIQUETA (o de CUALQUIER sinónimo si ``label`` es lista; matching
    por TOKENS, insensible a puntuación/acentos/case: 'I.V.A.'≡'iva'), devuelve
    (ventana, inicio_etiqueta_en_ventana, fin_etiqueta_en_ventana)."""
    labels = [label] if isinstance(label, str) else list(label)
    toks = _tokenize(ocr)
    out: list[tuple[str, int, int]] = []
    for lab_str in labels:
        lab = [t for t, _, _ in _tokenize(lab_str)]
        if not lab:
            continue
        ln = len(lab)
        for i in range(len(toks) - ln + 1):
            if all(toks[i + j][0] == lab[j] for j in range(ln)):
                ls, le = toks[i][1], toks[i + ln - 1][2]
                a = max(0, ls - _WIN_BEFORE)
                b = min(len(ocr), le + _WIN_AFTER)
                out.append((ocr[a:b], ls - a, le - a))
    return out


def _numbers_with_pos(text: str) -> list[tuple[Decimal, int, int, bool, str]]:
    """(magnitud, inicio, fin, es_porcentaje, moneda) de cada monto en el texto.
    ``es_porcentaje`` True si el número está seguido de '%' (no candidato a valor del campo);
    ``moneda`` = símbolo/código si lo había (€/EUR/$/USD…), o "" si no."""
    out: list[tuple[Decimal, int, int, bool, str]] = []
    for m in _NUM_TOKEN_RE.finditer(text):
        if not re.search(r"\d", m.group(0)):
            continue
        prefix = text[max(0, m.start() - 40):m.start()]
        suffix = text[m.end():m.end() + 16]   # ancho suficiente para "millones"/"miles"
        men = _classify_and_scale(m.group(0), prefix, suffix)
        if men.is_amount and men.magnitude is not None:
            out.append((men.magnitude, m.start(), m.end(),
                        suffix.lstrip().startswith("%"), men.currency))
    return out


def _snippet(ocr: str, start: int, end: int, radius: int = 30) -> str:
    """Excerpt limpio del OCR alrededor de [start, end) con elipsis. Para traza humana."""
    a, b = max(0, start - radius), min(len(ocr), end + radius)
    frag = re.sub(r"\s+", " ", ocr[a:b]).strip()
    return f"{'…' if a > 0 else ''}{frag}{'…' if b < len(ocr) else ''}"


def _label_positions(ocr: str, label: str | list[str]) -> list[tuple[int, int]]:
    """Posiciones absolutas (inicio, fin) de cada aparición de la etiqueta (o de cualquier
    sinónimo si ``label`` es lista; matching por tokens)."""
    labels = [label] if isinstance(label, str) else list(label)
    toks = _tokenize(ocr)
    out: list[tuple[int, int]] = []
    for lab_str in labels:
        lab = [t for t, _, _ in _tokenize(lab_str)]
        if not lab:
            continue
        ln = len(lab)
        for i in range(len(toks) - ln + 1):
            if all(toks[i + j][0] == lab[j] for j in range(ln)):
                out.append((toks[i][1], toks[i + ln - 1][2]))
    return out


def _locate_snippet(ocr: str, value: str) -> str | None:
    """Best-effort: ubica ``value`` en el OCR por alfanumérico (mapa de índices) y devuelve
    un excerpt. Sirve para identificadores siempre, y para fechas cuando los dígitos alinean."""
    target = _norm_alnum(value)
    if len(target) < 4:
        return None
    chars, idx = [], []
    for k, ch in enumerate(ocr):
        c = _norm_alnum(ch)
        if c:
            chars.append(c)
            idx.append(k)
    p = "".join(chars).find(target)
    if p < 0:
        return None
    return _snippet(ocr, idx[p], idx[p + len(target) - 1] + 1)


def _number_evidence(
    llm_value: str, ocr: str, label: str | list[str], verdict: EntityVerdict, matched: str | None,
) -> str | None:
    """Fragmento del OCR que justifica el veredicto numérico (READ-ONLY, no decide nada):
    SUPPORTED → el número anclado más cercano a la etiqueta; REVIEW → dónde está el valor
    colisionado; INVENTED → qué hay junto a la etiqueta (los candidatos que el humano vería)."""
    nums = _numbers_with_pos(ocr)
    if verdict is EntityVerdict.SUPPORTED and matched is not None:
        target = Decimal(matched)
        labels = _label_positions(ocr, label)
        best: tuple[int, int, int] | None = None
        for mag, s, e, pct, _cur in nums:
            if pct or not _NUMERIC._matches(target, mag):
                continue
            d = min((min(abs(s - le), abs(ls - s)) for ls, le in labels), default=0)
            if best is None or d < best[0]:
                best = (d, s, e)
        if best is not None:
            return _snippet(ocr, best[1], best[2])
    elif verdict is EntityVerdict.REVIEW:
        vals = [m.magnitude for m in parse_numeric_mentions(llm_value)
                if m.is_amount and m.magnitude is not None]
        for mag, s, e, _pct, _cur in nums:
            if any(_NUMERIC._matches(v, mag) for v in vals):
                return _snippet(ocr, s, e)
    elif verdict is EntityVerdict.INVENTED:
        labels = _label_positions(ocr, label)
        if labels:
            return _snippet(ocr, labels[0][0], labels[0][1])
    return None


def _anchor_number_role(
    llm_value: str, ocr: str, label: str | list[str], ocr_mags: list[Decimal],
    expected_currency: str | None = None,
) -> tuple[EntityVerdict, str | None, str]:
    """Anclaje por ROL/PROXIMIDAD con número MÁS CERCANO a la etiqueta (ADR-026). Devuelve
    (veredicto, valor_ocr_match, motivo):
      - valor == número NO-% MÁS CERCANO a la etiqueta → SUPPORTED (es el valor del campo).
        · CASO 1 moneda: si el LLM declara una moneda y el OCR muestra OTRA para ese importe
          → INVENTED (alucinación de moneda — espejo de UnitCurrencyJudge). Solo dispara cuando
          AMBOS lados traen moneda y difieren → cero FP en valores sin moneda (CASO 2).
      - existe en el OCR pero NO es el más cercano a su etiqueta → REVIEW (colisión/etiqueta).
      - no aparece en ninguna parte → INVENTED.
    Mismo normalizador de magnitud/moneda a ambos lados (parse_numeric_mentions)."""
    mentions: list[tuple[Decimal, str]] = [
        (m.magnitude, m.currency) for m in parse_numeric_mentions(llm_value)
        if m.is_amount and m.magnitude is not None]
    if not mentions:
        return EntityVerdict.REVIEW, None, "el valor no es numérico — revisar"
    # 1) ¿coincide con el número más cercano (no-%) a alguna aparición de la etiqueta?
    for window, lstart, lend in _label_occurrences(ocr, label):
        cands = [(min(abs(pos - lend), abs(lstart - pos)), mag, cur)
                 for mag, pos, _end, pct, cur in _numbers_with_pos(window) if not pct]
        if not cands:
            continue
        cands.sort(key=lambda c: c[0])
        _, nmag, ncur = cands[0]
        for vmag, vcur in mentions:
            if _NUMERIC._matches(vmag, nmag):
                if vcur and ncur and vcur != ncur:   # CASO 1: moneda contradicha
                    return (EntityVerdict.INVENTED, None,
                            f"moneda contradicha: el valor dice {vcur} pero el OCR "
                            f"muestra {ncur} para ese importe")
                if expected_currency:                # CURRENCY_AMOUNT: exige la divisa esperada
                    if not ncur:
                        return (EntityVerdict.REVIEW, None,
                                f"campo en {expected_currency}: divisa no confirmada en el OCR "
                                "junto al importe → revisar")
                    if ncur != expected_currency:
                        return (EntityVerdict.REVIEW, None,
                                f"divisa del documento ({ncur}) ≠ esperada "
                                f"({expected_currency}) → revisar")
                return EntityVerdict.SUPPORTED, str(nmag), "ancla junto a su etiqueta en el OCR"
    # 2) existe en el doc pero no como el valor más cercano a su etiqueta → revisar
    for vmag, _vcur in mentions:
        for s in ocr_mags:
            if _NUMERIC._matches(vmag, s):
                return (EntityVerdict.REVIEW, None,
                        "el valor existe en el OCR pero NO junto a su etiqueta "
                        "(posible colisión / etiqueta no reconocida) → revisar")
    # 3) tolerancia OCR NO-DESTRUCTIVA: ¿aparece si se pliegan confusiones en tokens numéricos
    #    (p.ej. "l.234,56" → "1.234,56")? clean_ocr_text solo toca tokens con dígitos; se usa solo
    #    como clave de comparación (fuente y valor quedan crudos). Si aparece así → REVIEW.
    from .clean import clean_ocr_text
    folded = [m.magnitude for m in parse_numeric_mentions(clean_ocr_text(ocr))
              if m.is_amount and m.magnitude is not None]
    for vmag, _vcur in mentions:
        if any(_NUMERIC._matches(vmag, s) for s in folded):
            return (EntityVerdict.REVIEW, None,
                    "el valor aparece en el OCR solo bajo tolerancia de confusión OCR → revisar")
    # 4) no está en ninguna parte
    return EntityVerdict.INVENTED, None, "el valor no está en el OCR"


# Tope de ventana para DATE/IDENTIFIER (el valor va justo DESPUÉS de la etiqueta). La ventana
# se corta además en el SALTO DE LÍNEA → aísla el valor del campo del de la línea siguiente
# (clave en facturas: 2 CUITs / 2 fechas en líneas distintas; un swap no debe pasar).
_WIN_ROLE = 80


def _label_window_texts(ocr: str, label: str | list[str], after: int) -> list[str]:
    """Sub-textos del OCR DESPUÉS de cada aparición de la etiqueta (o de CUALQUIER sinónimo si
    ``label`` es lista; matching por tokens), acotados al resto de la LÍNEA (corte en '\\n') y a
    ``after`` chars. Layout típico 'Etiqueta: valor' → la ventana es el valor del campo."""
    labels = [label] if isinstance(label, str) else list(label)
    toks = _tokenize(ocr)
    out: list[str] = []
    for lab_str in labels:
        lab = [t for t, _, _ in _tokenize(lab_str)]
        if not lab:
            continue
        ln = len(lab)
        for i in range(len(toks) - ln + 1):
            if all(toks[i + j][0] == lab[j] for j in range(ln)):
                le = toks[i + ln - 1][2]
                nl = ocr.find("\n", le)
                end = min(le + after, nl if nl >= 0 else len(ocr))
                out.append(ocr[le:end])
    return out


def _anchor_by_proximity(
    val: str, ocr: str, label: str | list[str], anchor_fn: Any,
) -> EntityVerdict:
    """Anclaje por ROL/PROXIMIDAD para DATE/IDENTIFIER (ADR-026). Reusa el anclaje por
    existencia (``anchor_fn``) pero acotado a la VENTANA de la etiqueta del campo:
      - ancla junto a la etiqueta → SUPPORTED (es el valor del campo, no el de otra parte).
      - existe en el OCR pero NO junto a su etiqueta → REVIEW (rol/colisión: p.ej. CUIT del
        receptor puesta como CUIT del emisor, o etiqueta no reconocida).
      - ambiguo (fecha dd/mm↔mm/dd) o corto → REVIEW. No está → INVENTED."""
    near = [anchor_fn(val, w) for w in _label_window_texts(ocr, label, _WIN_ROLE)]
    if EntityVerdict.SUPPORTED in near:
        return EntityVerdict.SUPPORTED
    whole = anchor_fn(val, ocr)
    if whole is EntityVerdict.SUPPORTED:
        return EntityVerdict.REVIEW          # existe pero no junto a su etiqueta → rol/colisión
    if EntityVerdict.REVIEW in near or whole is EntityVerdict.REVIEW:
        return EntityVerdict.REVIEW          # ambiguo / corto cerca de la etiqueta
    return EntityVerdict.INVENTED


def _anchor_percentage(
    llm_value: str, ocr: str, label: str | list[str],
) -> tuple[EntityVerdict, str | None, str]:
    """Ancla la magnitud de un número-% (los que NUMBER saltea) más cercano a la etiqueta.
    21% ≡ 21,0% ≡ 21. No-% junto a la etiqueta → INVENTED; en otro lado → REVIEW."""
    v_mags = [m.magnitude for m in parse_numeric_mentions(llm_value.replace("%", " "))
              if m.is_amount and m.magnitude is not None]
    if not v_mags:
        return EntityVerdict.REVIEW, None, "el valor no es un porcentaje numérico → revisar"
    for window, lstart, lend in _label_occurrences(ocr, label):
        cands = sorted((min(abs(pos - lend), abs(lstart - pos)), mag)
                       for mag, pos, _e, pct, _c in _numbers_with_pos(window) if pct)
        if cands and any(_NUMERIC._matches(vm, cands[0][1]) for vm in v_mags):
            return EntityVerdict.SUPPORTED, str(cands[0][1]), "el porcentaje ancla junto a su etiqueta"
    ocr_pct = [mag for mag, _s, _e, pct, _c in _numbers_with_pos(ocr) if pct]
    if any(_NUMERIC._matches(vm, p) for vm in v_mags for p in ocr_pct):
        return EntityVerdict.REVIEW, None, "el porcentaje existe pero no junto a su etiqueta → revisar"
    return EntityVerdict.INVENTED, None, "el porcentaje no está en el OCR"


# Confusiones típicas de OCR: letra → dígito que se le parece (0-9 cubiertos). SOLO para detectar
# corrupción y DEGRADAR INVENTED→REVIEW (nunca para confirmar — eso crearía falsos SUPPORTED).
_OCR_CONFUSE = {"o": "0", "d": "0", "l": "1", "i": "1", "|": "1", "z": "2", "e": "3",
                "a": "4", "s": "5", "g": "6", "t": "7", "b": "8", "q": "9"}


def _confusable_canon(s: str) -> str:
    """Colapsa caracteres OCR-confundibles a su clase de dígito; deja solo alfanum (minúsculas)."""
    return "".join(_OCR_CONFUSE.get(c, c) for c in s.lower() if c.isalnum())


def _ocr_garble_near(value: str, ocr: str, label: str) -> bool:
    """¿Hay junto a la etiqueta (o en el texto) un token MIXTO (con algún dígito) que es una
    versión OCR-confundida del valor (O↔0, l↔1, S↔5, E↔3, …)? Señal de que el valor podría estar
    pero el OCR lo dañó. Solo DEGRADA INVENTED→REVIEW. Piso de longitud + exigir ≥1 dígito en el
    token evita que palabras comunes ('total'→'70741') matcheen un valor numérico."""
    vc = _confusable_canon(value)
    if len(vc) < 5:
        return False
    windows = _label_window_texts(ocr, label, _WIN_AFTER) or [ocr]
    for w in windows:
        for tok in re.findall(r"[A-Za-z0-9.,/\-]+", w):
            low = tok.lower()
            # token MIXTO: algún dígito Y alguna letra confundible → firma de corrupción de OCR.
            # (un número limpio '100,00' no tiene letra → no es corrupción; una palabra no tiene dígito.)
            mixed = any(ch.isdigit() for ch in tok) and any(ch in _OCR_CONFUSE for ch in low)
            if mixed and _confusable_canon(tok) == vc:
                return True
    return False


def _anchor_entity(
    spec: EntitySpec, val: str, ocr: str, ocr_mags: list[Decimal],
) -> tuple[EntityVerdict, str | None, str, str | None]:
    """Veredicto + traza para una entidad. Tras el dispatch, si el veredicto es INVENTED pero hay
    un token OCR-confundido del valor junto a la etiqueta, DEGRADA a REVIEW (no acusa una
    corrupción de OCR como alucinación). Solo degrada; nunca confirma."""
    verdict, matched, reason, evidence = _anchor_entity_raw(spec, val, ocr, ocr_mags)
    if verdict is EntityVerdict.INVENTED and _ocr_garble_near(val, ocr, spec.name):
        return (EntityVerdict.REVIEW, None,
                "posible corrupción de OCR: el valor podría estar pero el texto fuente está "
                "dañado → revisar el escaneo", None)
    return verdict, matched, reason, evidence


def _anchor_entity_raw(
    spec: EntitySpec, val: str, ocr: str, ocr_mags: list[Decimal],
) -> tuple[EntityVerdict, str | None, str, str | None]:
    """Dispatch por tipo (sin null/scope/garble — eso lo deciden las capas de arriba)."""
    name, etype = spec.name, spec.type
    labels = [name, *spec.aliases]   # etiqueta + sinónimos declarados
    if etype is SemanticType.NUMBER or etype is SemanticType.CURRENCY_AMOUNT:
        exp_cur = spec.currency if etype is SemanticType.CURRENCY_AMOUNT else None
        verdict, matched, reason = _anchor_number_role(val, ocr, labels, ocr_mags, exp_cur)
        return verdict, matched, reason, _number_evidence(val, ocr, labels, verdict, matched)
    if etype is SemanticType.DATE:
        dayfirst = locale_dayfirst(spec.locale)
        verdict = _anchor_by_proximity(
            val, ocr, labels, lambda v, t, _df=dayfirst: anchor_date(v, t, _df))
        reason = {
            EntityVerdict.SUPPORTED: "la fecha ancla junto a su etiqueta (cualquier formato)",
            EntityVerdict.INVENTED: "la fecha no está en el OCR",
            EntityVerdict.REVIEW: "la fecha no ancla junto a su etiqueta "
                                  "(ambigua / rol / etiqueta no reconocida) → revisar",
        }[verdict]
        ev = _locate_snippet(ocr, val) if verdict is EntityVerdict.SUPPORTED else None
        return verdict, None, reason, ev
    if etype is SemanticType.PERIOD:
        verdict = _anchor_by_proximity(val, ocr, labels, anchor_period)
        reason = {
            EntityVerdict.SUPPORTED: "el período ancla junto a su etiqueta (cualquier formato)",
            EntityVerdict.INVENTED: "el período no está en el OCR",
            EntityVerdict.REVIEW: "el período no ancla junto a su etiqueta "
                                  "(rol / no parseable / etiqueta no reconocida) → revisar",
        }[verdict]
        ev = _locate_snippet(ocr, val) if verdict is EntityVerdict.SUPPORTED else None
        return verdict, None, reason, ev
    if etype is SemanticType.IDENTIFIER:
        verdict = _anchor_by_proximity(
            val, ocr, labels, lambda v, t, _fmt=spec.fmt: anchor_identifier(v, t, _fmt))
        reason = {
            EntityVerdict.SUPPORTED: "el identificador ancla junto a su etiqueta (normalizado)",
            EntityVerdict.INVENTED: "el identificador no está en el OCR",
            EntityVerdict.REVIEW: "el identificador no ancla junto a su etiqueta "
                                  "(rol/colisión, corto, o etiqueta no reconocida) → revisar",
        }[verdict]
        ev = _locate_snippet(ocr, val) if verdict is EntityVerdict.SUPPORTED else None
        return verdict, None, reason, ev
    if etype is SemanticType.PERCENTAGE:
        verdict, matched, reason = _anchor_percentage(val, ocr, labels)
        return verdict, matched, reason, None
    if etype is SemanticType.ENUM:
        opts = spec.enum or ()
        verdict = _anchor_by_proximity(val, ocr, labels, lambda v, t, _o=opts: anchor_enum(v, t, _o))
        reason = {
            EntityVerdict.SUPPORTED: "el valor ∈ conjunto y ancla junto a su etiqueta",
            EntityVerdict.REVIEW: "el valor está fuera del conjunto declarado, no ancla junto a su "
                                  "etiqueta, o no está groundeado → revisar",
        }.get(verdict, "revisar")
        ev = _locate_snippet(ocr, val) if verdict is EntityVerdict.SUPPORTED else None
        return verdict, None, reason, ev
    if etype is SemanticType.TEXT_ANCHORED:
        verdict = _anchor_by_proximity(val, ocr, labels, anchor_text)
        reason = {
            EntityVerdict.SUPPORTED: "el texto ancla verbatim junto a su etiqueta",
            EntityVerdict.REVIEW: "el texto no ancla verbatim junto a su etiqueta "
                                  "(reformulado / ausente / rol / muy corto) → revisar",
        }.get(verdict, "revisar")
        ev = _locate_snippet(ocr, val) if verdict is EntityVerdict.SUPPORTED else None
        return verdict, None, reason, ev
    # TEXT plano: prosa libre, sin anclaje genérico → UNVERIFIED honesto.
    return (EntityVerdict.UNVERIFIED, None,
            f"tipo '{etype.value}' sin extractor (fast-follow)", None)


def _pages(ocr: str) -> list[str]:
    """Páginas del OCR por el marcador de salto de página (form-feed ``\\f``, estándar de
    pdftotext). Sin marcador → una sola página (el documento entero)."""
    return ocr.split("\f")


def _anchor_scoped(
    spec: EntitySpec, val: str, ocr: str, ocr_mags: list[Decimal], pages: list[str],
) -> tuple[EntityVerdict, str | None, str, str | None]:
    """Anclaje ACOTADO a la página declarada por la entidad. Valor en la página → su veredicto.
    Valor ausente de la página pero presente en OTRA → REVIEW (existe, pero no donde debía; no se
    acusa INVENTED ni se confirma). Página inexistente → REVIEW."""
    p = spec.page or 0
    if not 1 <= p <= len(pages):
        return (EntityVerdict.REVIEW, None,
                f"la entidad declara página {p} pero el documento tiene {len(pages)} → revisar", None)
    verdict, matched, reason, evidence = _anchor_entity(
        spec, val, pages[p - 1], _ocr_magnitudes(pages[p - 1]))
    if verdict is EntityVerdict.INVENTED:
        whole, *_ = _anchor_entity(spec, val, ocr, ocr_mags)
        if whole is EntityVerdict.SUPPORTED:
            return (EntityVerdict.REVIEW, None,
                    f"el valor existe en el documento pero NO en la página declarada ({p}) → revisar",
                    None)
    return verdict, matched, reason, evidence


def match_coverage(
    output: dict[str, str | None],
    ocr: str,
    contract: list[EntitySpec],
) -> CoverageReport:
    """Verifica cobertura del contrato contra el OCR. Determinista, no bloquea.

    Asume ``output`` ya revalidado por Fase 0 (claves completas, string|null). El tipo de
    cada entidad sale del type-map (solo VERA); el LLM no lo provee. Si la entidad declara
    ``page``, el anclaje se acota a esa página del OCR (split por form-feed)."""
    ocr_mags = _ocr_magnitudes(ocr)
    pages = _pages(ocr)
    records: list[CoverageRecord] = []

    for spec in contract:
        val = output.get(spec.key)   # el dict de wire viene con claves slug
        if val is None:
            records.append(CoverageRecord(
                spec.name, spec.type, None, EntityVerdict.UNVERIFIED,
                "el LLM devolvió null (no encontrado)"))
            continue
        if spec.page is not None:
            verdict, matched, reason, evidence = _anchor_scoped(spec, val, ocr, ocr_mags, pages)
        else:
            verdict, matched, reason, evidence = _anchor_entity(spec, val, ocr, ocr_mags)
        records.append(CoverageRecord(spec.name, spec.type, val, verdict, reason,
                                      matched_source=matched, evidence=evidence))

    ok = not any(_blocks_ok(spec, r.verdict)
                 for spec, r in zip(contract, records, strict=True))
    return CoverageReport(ok=ok, records=records, llm_output=output)


def _blocks_ok(spec: EntitySpec, verdict: EntityVerdict) -> bool:
    """¿Esta entidad impide el ok global? SUPPORTED nunca bloquea; INVENTED (alucinación)
    SIEMPRE bloquea —aunque sea optional—; UNVERIFIED/REVIEW (no-confirmable) bloquea solo si la
    entidad es REQUERIDA. Así un TEXT optional (UNVERIFIED) no colapsa el ok de un doc real."""
    if verdict is EntityVerdict.SUPPORTED:
        return False
    if verdict is EntityVerdict.INVENTED:
        return True
    return not spec.optional


# ── Carga de contrato desde config (no hardcodeado) ─────────────────────────────

def load_contract(raw: dict[str, Any] | list[Any]) -> list[EntitySpec]:
    """Carga un contrato desde config: una lista de ``{"name","type"}`` o un dict con
    ``{"entities": [...]}``. ``type`` debe ser un valor de ``SemanticType``. La carga
    desde MongoDB/domain.yaml es un adapter delgado que llama acá.
    """
    entities = raw.get("entities", []) if isinstance(raw, dict) else raw
    contract: list[EntitySpec] = []
    for e in entities:
        if not isinstance(e, dict) or "name" not in e or "type" not in e:
            raise ValueError(f"entidad de contrato inválida: {e!r} (requiere name + type)")
        raw_fmt = e.get("format") or e.get("fmt")
        fmt = str(raw_fmt).lower() if raw_fmt else None
        if fmt is not None and fmt not in _KNOWN_FORMATS:
            raise ValueError(
                f"formato de identificador desconocido: {fmt!r} (válidos: "
                f"{sorted(_KNOWN_FORMATS)})")
        raw_cur = e.get("currency")
        currency = normalize_currency(str(raw_cur)) if raw_cur else None
        raw_loc = e.get("locale")
        locale = str(raw_loc) if raw_loc else None
        raw_page = e.get("page")
        page = int(raw_page) if raw_page is not None else None
        raw_enum = e.get("enum")
        enum = tuple(str(x) for x in raw_enum) if raw_enum else None
        raw_aliases = e.get("aliases") or ()
        aliases = tuple(str(x) for x in raw_aliases)
        contract.append(EntitySpec(
            name=str(e["name"]), type=SemanticType(str(e["type"])), fmt=fmt, currency=currency,
            locale=locale, page=page, optional=bool(e.get("optional", False)), enum=enum,
            aliases=aliases))
    # Claves de wire (slug) deben ser únicas: dos nombres que solo difieren en puntuación/
    # símbolos ("Total a pagar (€)" vs "Total a pagar $") slugifican igual → una entidad se
    # perdería silenciosa en el schema/output del LLM. Falla explícito en vez de pisar.
    keys = [s.key for s in contract]
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise ValueError(
            f"contrato con claves slug colisionantes: {dupes} — renombrá las entidades para "
            "que difieran en algo más que puntuación/símbolos")
    return contract
