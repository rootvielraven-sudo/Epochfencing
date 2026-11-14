# Autor: @Raven
"""VERA Tribunal de Evidencia — núcleo de jueces deterministas (Fase 1).

Ver ADR-022. Este módulo es ADITIVO: define el contrato de juez, los estados por
cifra, el normalizador de MAGNITUD CANÓNICA y el primer juez duro (NumericIntegrity).
NO está cableado todavía en VERAPipeline (eso es el cambio semántico — fase posterior).

Todo es determinista, local, sin LLM y sin I/O. Una sola pasada, reproducible.

Frontera de seguridad (ADR-022, innegociable): se verifica INTEGRIDAD (la cifra no se
corrompió y cuadra), NO corrección de dominio. Ningún veredicto afirma validez
clínica/legal.
"""
from __future__ import annotations

import os
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from .vera_types import (
    EntityVerdict,  # hogar canónico en vera_types (desacople de capas); se usa acá
)

# ── Estados ───────────────────────────────────────────────────────────────────

class FigureStatus(StrEnum):
    """Estado de una cifra del output frente a la fuente (jueces numéricos)."""
    SUPPORTED     = "supported"      # coincide con la fuente (magnitud canónica)
    CONTRADICTED  = "contradicted"   # choca con una cifra relacionada de la fuente — error duro
    NOT_FOUND     = "not_found"      # no está en la fuente — invento
    AMBIGUOUS     = "ambiguous"      # aparece pero no se resuelve inequívocamente → revisión
    OCR_UNCERTAIN = "ocr_uncertain"  # no verificable, fuente degradada → revisión (NO afirmar error)


# Estados que cuentan como ERROR duro (bloquean). AMBIGUOUS/OCR_UNCERTAIN NO bloquean.
_ERROR_STATES = frozenset({FigureStatus.CONTRADICTED, FigureStatus.NOT_FOUND})
_REVIEW_STATES = frozenset({FigureStatus.AMBIGUOUS, FigureStatus.OCR_UNCERTAIN})


# ── Mención numérica canónica ───────────────────────────────────────────────────

@dataclass(frozen=True)
class NumericMention:
    """Una cifra detectada en un texto, con su magnitud canónica resuelta.

    magnitude:  Decimal canónico (ya normalizado: separadores, escala, signo). None si
                no se pudo resolver una magnitud única.
    raw:        el token tal cual apareció (para evidencia).
    currency:   símbolo/código de moneda si lo había (€, EUR, $...), o "".
    is_amount:  True si es un MONTO; False si es fecha/ID/nº de referencia/año.
    ambiguous:  True si el formato admite más de una magnitud (p.ej. "1.234" = 1234 o 1.234).
    """
    raw:       str
    magnitude: Decimal | None
    currency:  str  = ""
    is_amount: bool = True
    ambiguous: bool = False


# ── Normalizador de magnitud canónica ──────────────────────────────────────────

# Símbolos con divisa PROPIA (sin ambigüedad). El '$' se trata aparte: es ambiguo (USD/ARS/…) y
# por defecto se interpreta ARS (peso, contexto AR de MINERVA), salvo US$/U$S/USD/dólar → USD.
_CURRENCY_SYMBOLS = {"€": "EUR", "£": "GBP", "¥": "JPY"}
_CURRENCY_CODES = {"EUR", "USD", "GBP", "JPY", "ARS", "MXN", "CLP", "COP", "BRL"}
_DOLLAR_USD_RE = re.compile(r"US\$|U\$S|USD|D[OÓ]LAR", re.IGNORECASE)

# El símbolo '$' es ambiguo (USD/ARS/MXN/CLP…). Su significado por defecto es CONFIGURABLE POR
# REGIÓN/DEPLOYMENT vía la env VERA_DOLLAR_DEFAULT (default 'ARS', contexto AR de MINERVA). La
# disambiguación US$/U$S/USD/dólar → USD se mantiene SIEMPRE, independiente del default.
_DOLLAR_DEFAULT = (os.getenv("VERA_DOLLAR_DEFAULT") or "ARS").strip().upper()


def set_dollar_default(code: str) -> None:
    """Override en runtime del significado del símbolo '$' (ej: 'USD' para deployment US/ES). Para
    tests o configuración programática; en producción usar la env VERA_DOLLAR_DEFAULT."""
    global _DOLLAR_DEFAULT
    _DOLLAR_DEFAULT = (code or "ARS").strip().upper()


def normalize_currency(c: str) -> str:
    """Divisa → código ISO en mayúsculas. '€'→'EUR', '$'→default por región (ARS), 'usd'→'USD'.
    Para comparar la divisa esperada del contrato (CURRENCY_AMOUNT) contra la del documento."""
    c = (c or "").strip()
    if c == "$":
        return _DOLLAR_DEFAULT
    return {"€": "EUR", "£": "GBP", "¥": "JPY"}.get(c, c.upper())
# Palabras de escala → multiplicador
_SCALE_WORDS = {
    "k": Decimal(10) ** 3, "mil": Decimal(10) ** 3,
    "m": Decimal(10) ** 6, "mm": Decimal(10) ** 6,
    "millon": Decimal(10) ** 6, "millón": Decimal(10) ** 6,
    "millones": Decimal(10) ** 6,
    "b": Decimal(10) ** 9, "billon": Decimal(10) ** 9, "billón": Decimal(10) ** 9,  # ES: 1e12; ambiguo — ver nota
}
# Números-palabra mínimos (ES) para "un millón", "dos millones", etc.
_WORD_UNITS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}

# Detección de fechas y referencias (NO son montos)
_DATE_RE = re.compile(
    r"\b(?:\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{4}[/\-]\d{1,2}[/\-]\d{1,2})\b"
)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
# Etiquetas que marcan un número como referencia/ID, no monto
_REF_LABEL_RE = re.compile(
    r"(?:n[ºo°.]|nro\.?|núm\.?|num\.?|ref\.?|referencia|expediente|exp\.?|cuenta|cta\.?|"
    r"iban|cuit|cuil|dni|nif|cif|factura\s+n|id)\s*[:#]?\s*$",
    re.IGNORECASE,
)


def canonical_magnitude(number_part: str) -> tuple[Decimal | None, bool]:
    """Resuelve la magnitud canónica de la parte numérica (sin moneda/escala/signo).

    Maneja separadores de miles/decimal:
      - ambos presentes  → el ÚLTIMO es decimal, el otro miles (1.234,56 ≡ 1,234.56).
      - un solo separador repetido (1.000.000) → miles.
      - un solo separador único → decimal si ≤2 dígitos a la derecha; si exactamente 3,
        se interpreta como miles pero se marca AMBIGUO (1.234 = 1234 o 1.234).

    Retorna (magnitud | None, ambiguo).
    """
    s = number_part.strip().replace(" ", "")
    if not s or not re.search(r"\d", s):
        return None, False

    has_dot = "." in s
    has_comma = "," in s
    ambiguous = False

    try:
        if has_dot and has_comma:
            dec_sep = "." if s.rfind(".") > s.rfind(",") else ","
            tho_sep = "," if dec_sep == "." else "."
            s2 = s.replace(tho_sep, "").replace(dec_sep, ".")
            return Decimal(s2), False

        if has_dot or has_comma:
            sep = "." if has_dot else ","
            count = s.count(sep)
            digits_after = len(s.rsplit(sep, 1)[1])
            if count > 1:
                # separador repetido → solo puede ser miles
                return Decimal(s.replace(sep, "")), False
            # un solo separador
            if digits_after == 3:
                # 1.234 / 1,234 — ambiguo: por defecto miles, marcado ambiguo
                ambiguous = True
                return Decimal(s.replace(sep, "")), ambiguous
            # decimal
            return Decimal(s.replace(",", ".") if sep == "," else s), False

        return Decimal(s), False
    except (InvalidOperation, ValueError):
        return None, False


# Escala por PALABRA en contexto monetario: "<escala> de …" o "<escala> <moneda>".
# Exige dinero después (de / símbolo / código / euros…) para no escalar "Mil gracias".
_WORD_SCALE_RE = re.compile(
    r"^\s*(mill[oó]n(?:es)?|mil(?:es)?)\b"
    r"(?:\s+de\b|\s*[€$£¥]|"
    r"\s+(?:euros?|d[oó]lares?|pesos?|libras?|EUR|USD|GBP|ARS|MXN|CLP|COP|BRL)\b)",
    re.IGNORECASE,
)


def _classify_and_scale(raw: str, prefix_context: str, suffix_context: str = "") -> NumericMention:
    """Toma un token numérico crudo + el texto inmediatamente anterior y devuelve la
    mención canónica (resuelve moneda, escala, signo, paréntesis contables, y si es monto).
    """
    token = raw.strip()
    currency = ""
    is_amount = True

    # Fecha → no es monto
    if _DATE_RE.fullmatch(token) or _DATE_RE.match(token):
        return NumericMention(raw=token, magnitude=None, is_amount=False)

    # Año suelto → no es monto
    digits_only = re.sub(r"\D", "", token)
    if _YEAR_RE.match(token):
        return NumericMention(raw=token, magnitude=None, is_amount=False)

    # Etiqueta de referencia justo antes → ID, no monto
    if _REF_LABEL_RE.search(prefix_context[-40:]):
        return NumericMention(raw=token, magnitude=None, is_amount=False)
    # Run de dígitos muy largo sin separadores decimales → probable IBAN/cuenta/ID.
    # El \)? final contempla el paréntesis contable: €(458.000.000,00) ES un monto, no un ID.
    if len(digits_only) >= 11 and not re.search(r"[.,]\d{1,2}\)?$", token):
        return NumericMention(raw=token, magnitude=None, is_amount=False)

    # Signo / paréntesis contables negativos
    sign = Decimal(1)
    body = token
    if body.startswith("(") and body.endswith(")"):
        sign = Decimal(-1)
        body = body[1:-1]
    if body.startswith("+"):
        body = body[1:]
    elif body.startswith("-"):
        sign = Decimal(-1)
        body = body[1:]

    # Símbolos con divisa propia (€/£/¥).
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in body:
            currency = code
            body = body.replace(sym, "")
    # Dólar '$' (ambiguo): por defecto ARS (peso, contexto AR); US$/U$S/USD/dólar → USD.
    dollar_ctx = prefix_context[-6:] + body + suffix_context[:6]
    if "$" in dollar_ctx:
        currency = "USD" if _DOLLAR_USD_RE.search(dollar_ctx) else _DOLLAR_DEFAULT
        body = body.replace("$", "")
    # Moneda como código en prefijo, cuerpo o sufijo (EUR 100 / 100 EUR). Busca entre TODOS los
    # triples de mayúsculas el que sea divisa — un label de 3 letras (IVA/CBU/DNI) NO debe tapar
    # la moneda real (era un bug: re.search agarraba el primero, p.ej. 'IVA', y descartaba 'EUR').
    window = prefix_context[-6:] + " " + body + " " + suffix_context[:6]
    triples: list[str] = re.findall(r"\b([A-Z]{3})\b", window)
    cur_code: str | None = next((c for c in triples if c in _CURRENCY_CODES), None)
    if cur_code:
        currency = cur_code
        body = re.sub(r"\b" + re.escape(cur_code) + r"\b", "", body)

    body = body.strip()

    # Escala como sufijo (1M, 1,5M, 2 MM, 100K)
    scale = Decimal(1)
    m_scale = re.match(r"^([\d.,]+)\s*(MM|M|K|B)\b", body, re.IGNORECASE)
    if m_scale:
        suffix = m_scale.group(2).lower()
        scale = _SCALE_WORDS.get(suffix, Decimal(1))
        body = m_scale.group(1)
    else:
        # Escala como PALABRA después del número, SOLO en contexto monetario: la escala va
        # seguida de "de" o de una moneda (euros/dólares/€/EUR…). Esto expande "1,5 millones
        # de EUR" o "5 mil euros", pero NO "1.000,00 Mil gracias" / "mil personas" (sin dinero).
        m_word = _WORD_SCALE_RE.match(suffix_context)
        if m_word:
            scale = Decimal(10) ** 6 if "mill" in m_word.group(1).lower() else Decimal(10) ** 3

    mag, ambiguous = canonical_magnitude(body)
    if mag is None:
        return NumericMention(raw=token, magnitude=None, currency=currency,
                              is_amount=is_amount, ambiguous=ambiguous)
    return NumericMention(raw=token, magnitude=sign * mag * scale, currency=currency,
                          is_amount=is_amount, ambiguous=ambiguous)


_NUM_TOKEN_RE = re.compile(
    # el número debe TERMINAR en dígito (no comerse el punto/coma final de oración)
    r"\(?\s*[+\-]?\s*[€$£¥]?\s*\d(?:[\d.,]*\d)?\s*(?:MM|M|K|B)?\b\)?",
    re.IGNORECASE,
)
# Números en palabras: "(un|dos|...) (millón|millones|mil)"
_WORD_NUM_RE = re.compile(
    r"\b(un|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(mil(?:lones|lón|lon)?|mil)\b",
    re.IGNORECASE,
)


def parse_numeric_mentions(text: str) -> list[NumericMention]:
    """Extrae todas las menciones numéricas de un texto con su magnitud canónica."""
    if not text:
        return []
    mentions: list[NumericMention] = []

    # Números en palabras
    for m in _WORD_NUM_RE.finditer(text):
        unit = _WORD_UNITS.get(m.group(1).lower(), 1)
        scale_key = m.group(2).lower()
        scale = _SCALE_WORDS.get("millon" if scale_key.startswith("mil") and "mill" in scale_key
                                 else "mil", Decimal(10) ** 3)
        if "mill" in scale_key:
            scale = Decimal(10) ** 6
        mentions.append(NumericMention(
            raw=m.group(0), magnitude=Decimal(unit) * scale, is_amount=True,
        ))

    # Spans de fecha — los números dentro de una fecha NO son montos
    date_spans = [m.span() for m in _DATE_RE.finditer(text)]

    # Números en cifras
    for m in _NUM_TOKEN_RE.finditer(text):
        raw = m.group(0)
        if not re.search(r"\d", raw):
            continue
        if any(m.start() < de and ds < m.end() for ds, de in date_spans):
            continue
        prefix = text[max(0, m.start() - 40):m.start()]
        suffix = text[m.end():m.end() + 16]   # ancho suficiente para "millones"/"miles"
        mention = _classify_and_scale(raw, prefix, suffix)
        if mention.magnitude is not None or not mention.is_amount:
            mentions.append(mention)
    return mentions


# ── Contrato del juez ───────────────────────────────────────────────────────────

@dataclass
class FigureFinding:
    """Hallazgo por cifra: estado + evidencia para auditoría."""
    output_raw: str
    status:     FigureStatus
    magnitude:  Decimal | None = None
    matched_source: str | None = None   # cifra de la fuente con la que se comparó
    note:       str = ""


@dataclass
class JudgeVerdict:
    """Veredicto de un juez sobre su faceta."""
    judge_id:  str
    is_hard:   bool                     # True → puede BLOQUEAR; False → solo REVIEW
    findings:  list[FigureFinding] = field(default_factory=list)

    @property
    def has_error(self) -> bool:
        """True si algún hallazgo es estado de error duro (CONTRADICTED/NOT_FOUND)."""
        return any(f.status in _ERROR_STATES for f in self.findings)

    @property
    def has_review(self) -> bool:
        """True si algún hallazgo va a revisión (AMBIGUOUS/OCR_UNCERTAIN)."""
        return any(f.status in _REVIEW_STATES for f in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "judge_id": self.judge_id,
            "is_hard":  self.is_hard,
            "findings": [
                {"output": f.output_raw, "status": f.status.value,
                 "magnitude": str(f.magnitude) if f.magnitude is not None else None,
                 "matched_source": f.matched_source, "note": f.note}
                for f in self.findings
            ],
        }


class EvidenceJudge(ABC):
    """Juez determinista que examina UNA faceta del viaje fuente→salida."""

    judge_id: str = "evidence_judge"
    is_hard:  bool = False          # heurísticos = False (solo REVIEW); duros = True
    applies_to: frozenset[str] = frozenset()   # tipos de doc donde es competente (vacío = todos)

    def competent_for(self, tipo_documento: str) -> bool:
        return not self.applies_to or tipo_documento.lower().strip() in self.applies_to

    @abstractmethod
    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        ...


# ── NumericIntegrityJudge (juez duro) ──────────────────────────────────────────

class NumericIntegrityJudge(EvidenceJudge):
    """Verifica que cada MONTO del output sobrevivió intacto frente a la fuente,
    comparando MAGNITUDES CANÓNICAS (no strings).

    Tolerancia: relativa, configurable y declarada. Solo absorbe redondeo de display
    (p.ej. €1.234,5 ≡ €1.234,50). NUNCA enmascara una diferencia no-cero relevante.
    """
    judge_id = "numeric_integrity"
    is_hard  = True

    # Banda de "corrupción": si el output no cuadra pero hay una fuente DENTRO de esta
    # banda relativa, es la misma cifra corrompida → CONTRADICTED. Fuera → NOT_FOUND.
    CONTRADICTION_BAND = Decimal("0.20")   # 20% relativo

    def __init__(self, abs_tolerance: Decimal = Decimal("0.005")) -> None:
        # ABSOLUTA, no relativa: 0.005 = medio centavo. Absorbe redondeo de display a
        # centavos pero NUNCA una diferencia real de centavos (una tolerancia relativa
        # enmascararía un centavo en un millón = fraude). Declarada y configurable.
        self.abs_tolerance = abs_tolerance

    def _matches(self, a: Decimal, b: Decimal) -> bool:
        return a == b or abs(a - b) <= self.abs_tolerance

    def _near(self, a: Decimal, b: Decimal) -> bool:
        """Misma cifra corrompida: diferencia relativa chica, o error de ESCALA (×10^k)."""
        denom = max(abs(a), abs(b), Decimal(1))
        if abs(a - b) / denom <= self.CONTRADICTION_BAND:
            return True
        if a == 0 or b == 0:
            return False
        # error de escala REAL: a == b·10^k (no un ratio "parecido" a 10)
        for k in range(1, 5):
            p = Decimal(10) ** k
            if self._matches(a, b * p) or self._matches(b, a * p):
                return True
        return False

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        source_amounts = [
            mtn.magnitude for mtn in parse_numeric_mentions(source_text)
            if mtn.is_amount and mtn.magnitude is not None
        ]

        for mtn in parse_numeric_mentions(output_text):
            if not mtn.is_amount or mtn.magnitude is None:
                continue
            m = mtn.magnitude
            if any(self._matches(m, s) for s in source_amounts):
                v.findings.append(FigureFinding(
                    output_raw=mtn.raw, status=FigureStatus.SUPPORTED, magnitude=m,
                ))
            elif mtn.ambiguous and any(self._near(m, s) for s in source_amounts):
                # formato ambiguo y hay una fuente cercana → no afirmamos error
                v.findings.append(FigureFinding(
                    output_raw=mtn.raw, status=FigureStatus.AMBIGUOUS, magnitude=m,
                    note="formato ambiguo, fuente cercana — a revisión",
                ))
            else:
                near = next((s for s in source_amounts if self._near(m, s)), None)
                if near is not None:
                    v.findings.append(FigureFinding(
                        output_raw=mtn.raw, status=FigureStatus.CONTRADICTED, magnitude=m,
                        matched_source=str(near),
                        note="misma cifra corrompida (no cuadra con la fuente)",
                    ))
                else:
                    v.findings.append(FigureFinding(
                        output_raw=mtn.raw, status=FigureStatus.NOT_FOUND, magnitude=m,
                        note="monto ausente en la fuente",
                    ))
        return v


# ── UnitCurrencyJudge (juez duro) ───────────────────────────────────────────────

class UnitCurrencyJudge(EvidenceJudge):
    """Verifica que cada monto del output preserva la MONEDA de la fuente. NumericIntegrity
    compara solo magnitud (€100 ≡ $100 → SUPPORTED); este juez atrapa el cambio de moneda.

    Solo se pronuncia cuando la magnitud SÍ está en la fuente (mismo monto) pero con otra
    moneda → CONTRADICTED. Si la magnitud no está, es asunto de NumericIntegrity (no duplica).
    """
    judge_id = "unit_currency"
    is_hard  = True

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        src_cur: dict[Decimal, set[str]] = {}
        for mtn in parse_numeric_mentions(source_text):
            if mtn.is_amount and mtn.magnitude is not None and mtn.currency:
                src_cur.setdefault(mtn.magnitude, set()).add(mtn.currency)
        for mtn in parse_numeric_mentions(output_text):
            if not mtn.is_amount or mtn.magnitude is None or not mtn.currency:
                continue
            src_curs = src_cur.get(mtn.magnitude)
            if src_curs and mtn.currency not in src_curs:
                v.findings.append(FigureFinding(
                    output_raw=mtn.raw, status=FigureStatus.CONTRADICTED,
                    magnitude=mtn.magnitude,
                    matched_source=f"{mtn.magnitude} {sorted(src_curs)}",
                    note=f"moneda cambiada: output={mtn.currency}, fuente={sorted(src_curs)}",
                ))
        return v


# ── ReferenceIntegrityJudge (juez duro) ─────────────────────────────────────────

# Números de identificación con etiqueta (expediente/artículo/nº/factura/cuenta) — los
# que NumericIntegrity descarta como no-monto. Un nº de expediente corrupto es un error grave.
_REF_NUM_RE = re.compile(
    r"(?:expediente|exp\.?|art[íi]culo|art\.?|n[ºo°]|nro\.?|núm\.?|ref\.?|referencia|"
    r"factura|cuenta|cta\.?)\s*[:#]?\s*(\d[\d./\-]*\d|\d+)",
    re.IGNORECASE,
)


def parse_reference_numbers(text: str) -> set[str]:
    """Extrae los números de identificación etiquetados (core de dígitos) de un texto."""
    refs: set[str] = set()
    for m in _REF_NUM_RE.finditer(text or ""):
        core = re.sub(r"\D", "", m.group(1))
        if core:
            refs.add(core)
    return refs


def _ref_similar(a: str, b: str) -> bool:
    """Misma longitud y ≥60% de dígitos coinciden en posición (referencia corrompida)."""
    if len(a) != len(b) or not a:
        return False
    same = sum(1 for x, y in zip(a, b, strict=False) if x == y)
    return same / len(a) >= 0.6


class ReferenceIntegrityJudge(EvidenceJudge):
    """Verifica que cada número de IDENTIFICACIÓN etiquetado del output (expediente, nº,
    artículo, factura, cuenta) aparezca en la fuente. NumericIntegrity los descarta como
    no-monto; aquí un expediente/artículo corrupto o inventado se detecta.

    Solo se pronuncia sobre referencias PRESENTES en el output (no exige que el output
    incluya todas las de la fuente — eso sería Completeness).
    """
    judge_id = "reference_integrity"
    is_hard  = True

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        src_refs = parse_reference_numbers(source_text)
        for m in _REF_NUM_RE.finditer(output_text or ""):
            core = re.sub(r"\D", "", m.group(1))
            if not core or core in src_refs:
                continue
            near = next((s for s in src_refs if _ref_similar(core, s)), None)
            if near is not None:
                v.findings.append(FigureFinding(
                    output_raw=m.group(0).strip(), status=FigureStatus.CONTRADICTED,
                    matched_source=near, note="referencia/ID corrompida (no coincide con la fuente)",
                ))
            else:
                v.findings.append(FigureFinding(
                    output_raw=m.group(0).strip(), status=FigureStatus.NOT_FOUND,
                    note="referencia/ID ausente en la fuente",
                ))
        return v


# ── CompletenessJudge (juez duro) — role-tagging de cifras CRÍTICAS ──────────────

# Etiquetas de cifra CRÍTICA (el resultado del documento). Role-tagging determinista por
# la etiqueta que precede a la cifra. NO incluye líneas de detalle: un resumen puede
# omitir una línea, pero nunca el total/saldo final/neto/patrimonio.
_CRITICAL_LABEL_RE = re.compile(
    r"(total(?:\s+a\s+pagar)?|importe\s+total|l[íi]quido(?:\s+a\s+percibir)?|"
    r"neto\s+a\s+percibir|saldo\s+final|patrimonio\s+neto|cantidad\s+reclamada|"
    r"importe\s+a\s+transferir|importe\s+recibido|precio\s+pactado|diferencia)"
    r"\s*[:=]?\s*[€$£¥]?\s*$",
    re.IGNORECASE,
)


def parse_critical_amounts(text: str) -> set[Decimal]:
    """Role-tagging: devuelve las magnitudes de las cifras CRÍTICAS (total/saldo final/
    neto/patrimonio/diferencia/cantidad reclamada/precio) según su etiqueta precedente.
    """
    crit: set[Decimal] = set()
    for m in _NUM_TOKEN_RE.finditer(text or ""):
        if not re.search(r"\d", m.group(0)):
            continue
        prefix = text[max(0, m.start() - 45):m.start()]
        if _CRITICAL_LABEL_RE.search(prefix):
            mtn = _classify_and_scale(m.group(0), prefix, text[m.end():m.end() + 16])
            if mtn.is_amount and mtn.magnitude is not None:
                crit.add(mtn.magnitude)
    return crit


class CompletenessJudge(EvidenceJudge):
    """Verifica que las cifras CRÍTICAS de la fuente (total, saldo final, neto, patrimonio,
    diferencia, cantidad reclamada, precio) aparezcan en el output. Atrapa la OMISIÓN de
    una cifra clave — algo que NumericIntegrity no ve (solo mira los montos PRESENTES).

    Por diseño NO exige las líneas de detalle (un resumen puede omitirlas) → evita falsos
    positivos en resúmenes selectivos. El role-tagging es por etiqueta, determinista.
    """
    judge_id = "completeness"
    is_hard  = True
    TOL = Decimal("0.005")

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        critical = parse_critical_amounts(source_text)
        out_mags = [mtn.magnitude for mtn in parse_numeric_mentions(output_text)
                    if mtn.is_amount and mtn.magnitude is not None]
        for c in critical:
            if not any(c == o or abs(c - o) <= self.TOL for o in out_mags):
                v.findings.append(FigureFinding(
                    output_raw=f"crítico={c}", status=FigureStatus.NOT_FOUND,
                    magnitude=c, note="cifra crítica de la fuente ausente en el output",
                ))
        return v


# ── ArithmeticCoherenceJudge (juez duro) — consistencia interna del documento ────

def _labeled_amounts(text: str) -> list[tuple[str, Decimal]]:
    """Devuelve (etiqueta_inmediata, magnitud) por cada monto. La etiqueta son los ~25
    caracteres justo antes de la cifra (la más cercana, no toda la oración)."""
    out: list[tuple[str, Decimal]] = []
    for m in _NUM_TOKEN_RE.finditer(text or ""):
        if not re.search(r"\d", m.group(0)):
            continue
        prefix = text[max(0, m.start() - 45):m.start()]
        mtn = _classify_and_scale(m.group(0), prefix, text[m.end():m.end() + 16])
        if mtn.is_amount and mtn.magnitude is not None:
            out.append((prefix[-25:].lower(), mtn.magnitude))
    return out


# Relaciones aritméticas: (etiqueta_resultado, [(etiqueta_componente, signo)], nombre).
# Solo se evalúa una relación si TODAS sus etiquetas están presentes → no dispara en
# documentos que no son de ese tipo (evita falsos positivos).
_RELATIONS: list[tuple[str, list[tuple[str, int]], str]] = [
    ("total a pagar",        [("base imponible", 1), ("iva", 1)],               "factura"),
    ("l[íi]quido a percibir", [("salario bruto", 1), ("deducciones", -1)],       "nomina"),
    ("patrimonio neto",      [("activo total", 1), ("pasivo total", -1)],        "balance"),
    ("diferencia",           [("seg[úu]n libros", 1), ("seg[úu]n banco", -1)],   "conciliacion"),
    ("saldo final",          [("saldo anterior", 1), ("movimiento", 1)],         "extracto"),
    ("total a pagar",        [("principal", 1), ("costas", 1)],                   "embargo"),
]


class ArithmeticCoherenceJudge(EvidenceJudge):
    """Verifica la consistencia INTERNA del documento: Σlíneas = total, IVA = base+iva,
    activo − pasivo = patrimonio, etc. Ortogonal a la fidelidad output↔fuente: detecta un
    documento cuya aritmética no cierra aunque el output lo reproduzca fielmente.

    Conservador: solo evalúa una relación si TODAS sus etiquetas están presentes (un único
    resultado y sus componentes) → no dispara en documentos ajenos, evitando falsos positivos.
    Corre sobre la FUENTE (la verdad documental).
    """
    judge_id = "arithmetic_coherence"
    is_hard  = True
    TOL = Decimal("0.05")   # tolera centavos de redondeo acumulados, no un descuadre real

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        # ROLE-BINDING aritmético: la relación debe cerrar tanto en la FUENTE (doc mal armado)
        # como en el OUTPUT (cifra corrompida que rompe la relación). Esto atrapa scale10
        # relacional: corromper ×10 un componente (IVA×10=base) da una magnitud que SÍ existe
        # en la fuente —NumericIntegrity la pasa— pero la relación del output ya no cierra.
        for which, text in (("fuente", source_text), ("output", output_text)):
            self._check_relations(text, which, v)
        return v

    def _check_relations(self, text: str, which: str, v: JudgeVerdict) -> None:
        labeled = _labeled_amounts(text)
        for result_re, comps, name in _RELATIONS:
            results = [mag for lbl, mag in labeled if re.search(result_re, lbl)]
            if len(results) != 1:
                continue
            comp_sum = Decimal(0)
            complete = True
            for comp_re, sign in comps:
                vals = [mag for lbl, mag in labeled if re.search(comp_re, lbl)]
                if not vals:
                    complete = False
                    break
                comp_sum += Decimal(sign) * sum(vals, Decimal(0))
            if not complete:
                continue
            result = results[0]
            if abs(comp_sum - result) > self.TOL:
                v.findings.append(FigureFinding(
                    output_raw=f"{name}: Σcomponentes={comp_sum} ≠ resultado={result}",
                    status=FigureStatus.CONTRADICTED, magnitude=result,
                    matched_source=str(comp_sum),
                    note=f"aritmética interna inconsistente ({name}, {which})",
                ))


# ── LexicalGroundingJudge (juez heurístico — solo REVIEW, nunca BLOCK) ──────────

class LexicalGroundingJudge(EvidenceJudge):
    """Envuelve el grounding léxico actual de VERA. Heurístico: sin LLM no tiene
    autoridad para AFIRMAR alucinación en prosa → solo emite REVIEW, nunca bloquea solo.

    Traza visible (ADR-022 paso 5): emite UN finding por CLAIM sin soporte léxico
    (``output_raw`` = el claim concreto, truncado a 100 chars), no un conteo a nivel
    documento. Así el REVIEW es atribuible: señala CUÁL claim de la prosa no ancla en la
    fuente, no solo que existe alguno. Si el score global es bajo pero el holón no listó
    claims (defensivo), cae a un finding a nivel documento.
    """
    judge_id = "lexical_grounding"
    is_hard  = False
    REVIEW_THRESHOLD = 0.70

    def __init__(self) -> None:
        # PORT NOTE (MIKA): the holon-based lexical grounding backend is not ported.
        # The coverage/anchoring path (framework/vera/engine.py) does not use this judge.
        # Instantiating it is intentionally unsupported until a grounding backend is wired.
        raise NotImplementedError(
            "LexicalGroundingJudge requires a grounding backend not ported to MIKA; "
            "use the coverage/anchoring path instead.")

    def judge(self, output_text: str, source_text: str,
              context: dict[str, object] | None = None) -> JudgeVerdict:
        v = JudgeVerdict(judge_id=self.judge_id, is_hard=self.is_hard)
        gr = self._holon.analyze(resumen=output_text, texto_fuente=source_text)
        if gr.grounding_score < self.REVIEW_THRESHOLD:
            if gr.ungrounded:
                # ATRIBUCIÓN POR CLAIM: un finding por claim sin soporte → traza accionable
                for claim in gr.ungrounded:
                    v.findings.append(FigureFinding(
                        output_raw=claim,
                        status=FigureStatus.AMBIGUOUS,   # estado de REVISIÓN (no error)
                        note=f"claim sin soporte léxico (grounding={gr.grounding_score:.2f}) — revisión",
                    ))
            else:
                # defensivo: score bajo sin claims listados → finding a nivel documento
                v.findings.append(FigureFinding(
                    output_raw=f"grounding={gr.grounding_score:.2f}",
                    status=FigureStatus.AMBIGUOUS,
                    note="grounding global bajo — revisión",
                ))
        return v


# ── Agregación (sin promediar) ──────────────────────────────────────────────────

class PanelVerdict(StrEnum):
    OK            = "ok"
    WARNING       = "warning"
    HALLUCINATION = "hallucination"


@dataclass
class TribunalResult:
    """Resultado del panel: veredicto + jueces + evidencia bloqueante/revisión."""
    tribunal:          str
    verdict:           PanelVerdict
    judges:            list[JudgeVerdict]  = field(default_factory=list)
    blocking_evidence: list[FigureFinding] = field(default_factory=list)
    review_evidence:   list[FigureFinding] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "tribunal": self.tribunal,
            "verdict":  self.verdict.value,
            "judges":   [j.to_dict() for j in self.judges],
            "blocking_evidence": [
                {"output": f.output_raw, "status": f.status.value, "note": f.note,
                 "matched_source": f.matched_source}
                for f in self.blocking_evidence
            ],
            "review_evidence": [
                {"output": f.output_raw, "status": f.status.value, "note": f.note}
                for f in self.review_evidence
            ],
        }


def classify_entities(
    verdicts: list[JudgeVerdict],
) -> list[tuple[EntityVerdict, FigureFinding]]:
    """Deriva el veredicto POR ENTIDAD desde los findings de los jueces (ADR-022, pilar
    de cobertura). Determinista. Cada record SIEMPRE proviene de un finding (= un juez
    miró esa entidad), por lo que solo emite TRES estados:
      - SUPPORTED (juez duro)                       → EntityVerdict.SUPPORTED
      - CONTRADICTED/NOT_FOUND (juez duro)          → EntityVerdict.INVENTED (bloqueo)
      - AMBIGUOUS/OCR_UNCERTAIN, o error heurístico → EntityVerdict.REVIEW (un juez la
                                                       miró pero quedó dudosa)

    NUNCA emite EntityVerdict.UNVERIFIED: ese estado es AUSENCIA total de finding y solo
    lo deriva la capa de cobertura (punto 2: entidad declarada en contrato sin finding).
    La ausencia no produce record acá — no hay nada que clasificar.

    Traza visible (paso 5): `LexicalGroundingJudge` ahora emite un finding por CLAIM sin
    soporte (output_raw = el claim), por lo que cada REVIEW de prosa es atribuible al claim
    concreto que falla — no a un conteo a nivel documento.
    """
    records: list[tuple[EntityVerdict, FigureFinding]] = []
    for jv in verdicts:
        for f in jv.findings:
            if f.status in _ERROR_STATES:
                # juez duro → INVENTED (bloqueo); heurístico con 'error' → REVIEW (no bloquea)
                records.append(
                    (EntityVerdict.INVENTED if jv.is_hard else EntityVerdict.REVIEW, f)
                )
            elif f.status in _REVIEW_STATES:
                records.append((EntityVerdict.REVIEW, f))
            elif f.status == FigureStatus.SUPPORTED:
                records.append((EntityVerdict.SUPPORTED, f))
    return records


def _declared_names(contract: list[object] | None) -> list[str]:
    """Nombres de entidad declarados en el contrato (solo NOMBRES, nunca valores).
    Acepta una lista de strings o de dicts ``{"name": ...}``. El CABLEADO/carga del
    contrato desde la config es el punto 2; acá solo se leen los nombres ya provistos.
    """
    names: list[str] = []
    for e in contract or []:
        if isinstance(e, str):
            names.append(e)
        elif isinstance(e, dict) and e.get("name"):
            names.append(str(e["name"]))
    return names


def aggregate(
    verdicts: list[JudgeVerdict],
    contract: list[object] | None = None,
    level: str = "asistencia",
) -> tuple[PanelVerdict, list[FigureFinding], list[FigureFinding]]:
    """Combina veredictos SIN promediar (ADR-022 §3) con COBERTURA CERRADA.

    Bloqueo/revisión (igual que siempre):
    - juez DURO con estado de error (CONTRADICTED/NOT_FOUND) → BLOQUEA → HALLUCINATION.
    - juez heurístico: su 'error' se degrada a REVIEW (sin LLM no afirma alucinación).
    - cualquier REVIEW (y 0 bloqueo) → WARNING.

    Cobertura (el cambio del pilar):
    - **Contrato VACÍO/None (default):** comportamiento IDÉNTICO al histórico —
      todo limpio → OK. (Transparente: no altera seed-42.)
    - **Contrato declarado:** OK global SOLO si todo el contrato quedó SUPPORTED.
      NUNCA OK por ausencia de findings — un contrato cuya cobertura no se confirma va
      a revisión (WARNING), jamás a OK silencioso. (La confirmación nombre/tipo de cada
      entidad se completa en el punto 2; acá la regla ya no permite OK por omisión.)
    """
    blocking: list[FigureFinding] = []
    review:   list[FigureFinding] = []
    for jv in verdicts:
        for f in jv.findings:
            if f.status in _ERROR_STATES:
                (blocking if jv.is_hard else review).append(f)
            elif f.status in _REVIEW_STATES:
                review.append(f)

    # ── Contrato VACÍO → transparente: misma decisión de siempre. ────────────────
    if not _declared_names(contract):
        if blocking:
            return PanelVerdict.HALLUCINATION, blocking, review
        if review:
            return PanelVerdict.WARNING, blocking, review
        return PanelVerdict.OK, blocking, review

    # ── Contrato declarado → COBERTURA CERRADA. ──────────────────────────────────
    if blocking:
        return PanelVerdict.HALLUCINATION, blocking, review
    if review:
        return PanelVerdict.WARNING, blocking, review
    # Sin bloqueo ni revisión: OK SOLO si CADA nombre declarado tiene un SUPPORTED que
    # lo ancla. El nombre del finding (role-tag) llega en el punto 3 (entity_name); hasta
    # entonces, una entidad declarada sin SUPPORTED confirmado NO produce OK → revisión.
    supported = {
        getattr(f, "entity_name", "") for v, f in classify_entities(verdicts)
        if v is EntityVerdict.SUPPORTED
    }
    unmet = [n for n in _declared_names(contract) if n not in supported]
    if unmet:
        return PanelVerdict.WARNING, blocking, review   # nunca OK por ausencia/omisión
    return PanelVerdict.OK, blocking, review


# ── Router → tribunales especializados ──────────────────────────────────────────

@dataclass
class Tribunal:
    """Panel especializado: conjunto de jueces para un tipo de documento."""
    name:   str
    judges: list[EvidenceJudge]


# Keywords por dominio para el dispatch DETERMINISTA (no LLM). Reusa la señal de tipo
# de documento que ya entra al pipeline; ante desconocido/ambiguo → tribunal general.
_TYPE_KEYWORDS: dict[str, frozenset[str]] = {
    "contable": frozenset({"factura", "extracto", "balance", "conciliacion", "nomina",
                           "recibo", "asiento", "contable", "iva", "remito", "financ", "banc"}),
    "legal":    frozenset({"embargo", "demanda", "sentencia", "contrato", "escritura",
                           "diligencia", "poder", "legal", "expediente", "juzgado"}),
    "receta":   frozenset({"receta", "prescripcion", "prescripción", "dosis",
                           "medicament", "clinic", "clínic", "medic"}),
}


def route_key(tipo_documento: str) -> str:
    """Mapea tipo de documento → clave de tribunal. Determinista. Fail-safe a 'general'."""
    t = (tipo_documento or "").lower().strip()
    if not t:
        return "general"
    matches = [key for key, kws in _TYPE_KEYWORDS.items() if any(kw in t for kw in kws)]
    # 0 matches (desconocido) o >1 (ambiguo/mezcla) → general (superset): falla hacia MÁS escrutinio.
    return matches[0] if len(matches) == 1 else "general"


class VERATribunalRouter:
    """Despacha por tipo de documento al tribunal especializado. Tipo desconocido o
    ambiguo → tribunal GENERAL (superset): falla hacia MÁS escrutinio, nunca menos (ADR-022 §4).
    El dispatch es DETERMINISTA (keywords sobre la señal de tipo ya existente), no un LLM.
    """

    def __init__(self, tribunals: dict[str, Tribunal], general: Tribunal) -> None:
        self.tribunals = tribunals
        self.general   = general

    def route(self, tipo_documento: str) -> Tribunal:
        return self.tribunals.get(route_key(tipo_documento), self.general)

    def run(self, output_text: str, source_text: str,
            tipo_documento: str = "",
            context: dict[str, object] | None = None) -> TribunalResult:
        tribunal = self.route(tipo_documento)
        verdicts = [
            j.judge(output_text, source_text, context)
            for j in tribunal.judges if j.competent_for(tipo_documento)
        ]
        verdict, blocking, review = aggregate(verdicts)
        return TribunalResult(
            tribunal=tribunal.name, verdict=verdict,
            judges=verdicts, blocking_evidence=blocking, review_evidence=review,
        )


def build_default_router() -> VERATribunalRouter:
    """Router por defecto con los jueces de Fase 1 (numérico duro + grounding heurístico).
    Extensible: agregar jueces/tribunales sin tocar el motor.
    """
    numeric   = NumericIntegrityJudge()
    grounding = LexicalGroundingJudge()
    currency  = UnitCurrencyJudge()
    reference = ReferenceIntegrityJudge()
    complete  = CompletenessJudge()
    arith     = ArithmeticCoherenceJudge()
    return VERATribunalRouter(
        tribunals={
            "contable": Tribunal("contable", [numeric, currency, reference, complete, arith, grounding]),
            "legal":    Tribunal("legal",    [reference, numeric, currency, complete, grounding]),
            "receta":   Tribunal("receta",   [numeric, grounding]),
        },
        general=Tribunal("general", [numeric, currency, reference, complete, arith, grounding]),
    )


def build_contradiction_router() -> VERATribunalRouter:
    """Router para la DIMENSIÓN DE CONTRADICCIÓN del veredicto combinado de VERA (opt-in
    ``VERA_CONTRADICTION``). Usa SOLO jueces DUROS deterministas, sin LLM ni I/O:
    numérico / moneda / referencia / aritmética interna (los que pueden emitir
    ``CONTRADICTED`` con evidencia real) más completitud.

    A diferencia de ``build_default_router``, NO instancia ``LexicalGroundingJudge``: ese
    juez es heurístico (nunca bloquea por sí solo) y además requiere un backend de
    grounding que no está portado a MIKA (su ``__init__`` levanta ``NotImplementedError``).
    Omitirlo mantiene este router construíble y totalmente determinista/reproducible.
    """
    numeric   = NumericIntegrityJudge()
    currency  = UnitCurrencyJudge()
    reference = ReferenceIntegrityJudge()
    complete  = CompletenessJudge()
    arith     = ArithmeticCoherenceJudge()
    full = [numeric, currency, reference, complete, arith]
    return VERATribunalRouter(
        tribunals={
            "contable": Tribunal("contable", full),
            "legal":    Tribunal("legal",    [reference, numeric, currency, complete]),
            "receta":   Tribunal("receta",   [numeric]),
        },
        general=Tribunal("general", full),
    )
