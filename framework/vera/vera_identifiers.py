# Autor: @Raven
"""VERA — anclaje canónico de IDENTIFICADORES (ADR-022 paso 4 + P0.1). Determinista, sin LLM.

IBAN / CUIT / CUIL / NIF / CIF / DNI / DOI / nº de cuenta/expediente. Anclaje = EXISTENCIA
canónica del identificador como TOKEN del OCR (no substring embebido), con FRONTERA de token:

  - GENÉRICO (sin ``fmt``): el valor canónico debe IGUALAR un run de identificador del OCR
    (alfanumérico unido por '.', '/' o '-' — NUNCA por espacio, que es ambiguo entre separador
    interno y frontera de campo). Igualdad, no substring → mata el falso-SUPPORTED por embebido
    o por concatenación de tokens adyacentes.
  - CON ``fmt`` (iban/cuit/cuil/nif/cif): se extraen candidatos por el PATRÓN del formato
    (tolera espacios internos, p.ej. IBAN "ES.. .... ....") y se valida CHECKSUM determinista.
    Checksum inválido → NUNCA SUPPORTED (REVIEW si está en el doc, INVENTED si no).

Filosofía regulatoria: cero falsos SUPPORTED. Lo dudoso (embebido, corto, checksum inválido
pero presente) → REVIEW. Lo ausente → INVENTED. (null → UNVERIFIED lo decide match_coverage.)
"""
from __future__ import annotations

import re

from .vera_types import EntityVerdict

# Mínimo de caracteres canónicos para anclar (evita matches triviales de runs cortos).
_MIN_LEN = 6
# Canónico: sin espacios/puntos/barras/guiones, en mayúsculas (simétrico a ambos lados).
_SEP_RE = re.compile(r"[\s./\-]")
# Run de identificador GENÉRICO: alfanumérico unido SOLO por '.', '/' o '-' (no espacio).
# Un match exacto contra un run TIGHT habilita SUPPORTED.
_RUN_RE = re.compile(r"[A-Za-z0-9]+(?:[./\-][A-Za-z0-9]+)*")
# Run LOOSE: también bridgea ESPACIO simple. Solo sirve para DEGRADAR a REVIEW (no a INVENTED)
# un valor que aparecería si juntáramos tokens separados por espacio — ambiguo entre identificador
# espaciado (IBAN) y concatenación de campos. NUNCA habilita SUPPORTED (eso exige fmt+checksum).
_LOOSE_RUN_RE = re.compile(r"[A-Za-z0-9]+(?:[ ./\-][A-Za-z0-9]+)*")

# Patrones de FORMATO (toleran espacios/guiones internos) para extraer candidatos del OCR.
_FMT_RE: dict[str, re.Pattern[str]] = {
    "iban": re.compile(r"[A-Z]{2}\d{2}(?:[ \-]?[A-Za-z0-9]){11,30}", re.IGNORECASE),
    "cuit": re.compile(r"\d{2}[ \-]?\d{8}[ \-]?\d"),
    "cuil": re.compile(r"\d{2}[ \-]?\d{8}[ \-]?\d"),
    "nif":  re.compile(r"\d{8}[ \-]?[A-Za-z]"),
    "cif":  re.compile(r"[A-Za-z][ \-]?\d{7}[ \-]?[A-Za-z0-9]"),
}


def canon_id(s: str) -> str:
    """Forma canónica: sin espacios/puntos/barras/guiones, en mayúsculas."""
    return _SEP_RE.sub("", s or "").upper()


# Tolerancia OCR NO-DESTRUCTIVA: pliega confusiones típicas (O↔0, l/I/L↔1, S↔5, B↔8, Z↔2, G↔6)
# a un dígito representativo. Se aplica SIMÉTRICAMENTE a ambos lados SOLO como clave de comparación
# (el valor y la fuente quedan crudos). Así "20…" ancla contra un OCR degradado "2O…" sin reescribir
# nada, y "DCS30…" contra "DCS30…" sigue anclando (ambos pliegan igual). No corrompe el output.
_FOLD = str.maketrans({"O": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})


def _fold(s: str) -> str:
    return (s or "").translate(_FOLD)


def _runs(ocr: str) -> list[str]:
    """Runs de identificador del OCR (genéricos), canonizados."""
    return [canon_id(m.group(0)) for m in _RUN_RE.finditer(ocr or "")]


# ── Checksums deterministas ──────────────────────────────────────────────────
def _iban_ok(c: str) -> bool:
    c = c.upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]+", c) or not (15 <= len(c) <= 34):
        return False
    r = c[4:] + c[:4]
    return int("".join(str(int(ch, 36)) for ch in r)) % 97 == 1


def _cuit_ok(c: str) -> bool:
    if not re.fullmatch(r"\d{11}", c):
        return False
    w = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(c[i]) * w[i] for i in range(10))
    dv = 11 - (s % 11)
    dv = 0 if dv == 11 else (9 if dv == 10 else dv)
    return dv == int(c[10])


def _nif_ok(c: str) -> bool:
    c = c.upper()
    if not re.fullmatch(r"\d{8}[A-Z]", c):
        return False
    return "TRWAGMYFPDXBNJZSQVHLCKE"[int(c[:8]) % 23] == c[8]


def _cif_ok(c: str) -> bool:
    c = c.upper()
    if not re.fullmatch(r"[A-Z]\d{7}[0-9A-J]", c):
        return False
    s = 0
    for i, ch in enumerate(c[1:8]):
        n = int(ch)
        if i % 2 == 0:                 # posición impar (1-based) → ×2 y sumar dígitos
            n *= 2
            n = n // 10 + n % 10
        s += n
    ctrl = (10 - (s % 10)) % 10
    return c[8] == str(ctrl) or c[8] == "JABCDEFGHI"[ctrl]   # control dígito o letra


_CHECKSUM = {"iban": _iban_ok, "cuit": _cuit_ok, "cuil": _cuit_ok,
             "nif": _nif_ok, "cif": _cif_ok}

# Formatos declarables en el contrato (habilitan checksum). Lo consume load_contract.
KNOWN_FORMATS = frozenset(_CHECKSUM)


def _locate(llm_canon: str, ocr: str, fmt: str | None) -> str:
    """Dónde aparece el valor en el OCR: 'exact' / 'embedded' / 'loose' / 'absent'.

    - exact   : iguala un identificador anclable (run tight por '.'/'/'/'-', o candidato del
                formato declarado) → habilita SUPPORTED.
    - embedded: substring DENTRO de un identificador mayor → REVIEW (no afirmamos).
    - loose   : aparece solo si bridgeamos ESPACIOS (sin fmt) → ambiguo → REVIEW (no INVENTED:
                podría ser un identificador espaciado real; no acusamos en falso).
    - absent  : no está en ninguna forma → INVENTED.
    """
    llm_f = _fold(llm_canon)   # clave tolerante a confusión OCR (no reescribe el valor)
    if fmt and fmt in _FMT_RE:
        cands = {canon_id(m.group(0)) for m in _FMT_RE[fmt].finditer(ocr or "")}
        if llm_canon in cands:
            return "exact"
        # tolerancia OCR: candidatos del OCR plegado (recupera "2O…" degradado) → REVIEW, no SUPPORTED
        cands_f = {canon_id(m.group(0)) for m in _FMT_RE[fmt].finditer(_fold(ocr or ""))}
        if llm_f in cands_f or llm_f in {_fold(c) for c in cands}:
            return "ocr"
        return "embedded" if any(llm_canon in c for c in cands) else "absent"
    tight = _runs(ocr)
    if llm_canon in tight:
        return "exact"
    if llm_f in {_fold(r) for r in tight}:
        return "ocr"                             # recuperado bajo confusión OCR → REVIEW (no SUPPORTED)
    if any(llm_canon in r for r in tight):
        return "embedded"
    loose = [canon_id(m.group(0)) for m in _LOOSE_RUN_RE.finditer(ocr or "")]
    return "loose" if llm_canon in loose else "absent"


def anchor_identifier(llm_value: str, ocr: str, fmt: str | None = None) -> EntityVerdict:
    """Veredicto de cobertura para una entidad IDENTIFIER. Determinista.

    SUPPORTED : el valor IGUALA un identificador anclable del OCR — run unívoco (sep '.'/'/'/'-'),
                o candidato del formato declarado con CHECKSUM válido. Nunca por espacios.
    REVIEW    : dudoso — embebido en un identificador mayor, presente solo bridgeando espacios
                (sin formato declarado), demasiado corto, o (con fmt) checksum inválido pero
                presente. Va a revisión humana; no afirmamos ni acusamos.
    INVENTED  : ausente del OCR en toda forma anclable.
    (null → UNVERIFIED lo decide match_coverage antes de llamar acá.)
    """
    llm_canon = canon_id(llm_value)
    if len(llm_canon) < _MIN_LEN:
        return EntityVerdict.REVIEW          # demasiado corto → no afirmamos
    chk = _CHECKSUM.get(fmt) if fmt else None
    if chk is not None and not chk(llm_canon):
        # formato declarado + checksum inválido → NUNCA SUPPORTED.
        return (EntityVerdict.REVIEW if _locate(llm_canon, ocr, fmt) != "absent"
                else EntityVerdict.INVENTED)
    where = _locate(llm_canon, ocr, fmt)
    if where == "exact":
        return EntityVerdict.SUPPORTED
    if where in ("embedded", "loose", "ocr"):
        return EntityVerdict.REVIEW          # ambiguo / recuperado bajo confusión OCR → revisión humana
    return EntityVerdict.INVENTED
