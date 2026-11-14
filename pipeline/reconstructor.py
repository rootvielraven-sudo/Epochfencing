# Autor: @Raven
"""Reconstructores LLM-GSR: de la factura OCR degradada → extracción estructurada.

Fiel al paper *LLM-Guided Sequence Reconstruction (LLM-GSR)*:

  R(ĉ, φ) = argmax_s P(s | ĉ, φ)          (Ec. 3)

el reconstructor recupera cada unidad atómica ĉ (acá: una factura) condicionado al contexto
de dominio φ (``DomainContext``: doc_type, esquema de campos, evidencia = OCR). El Dispatcher
Ray NO reconstruye: solo reparte y agrega (patrón *Pure Dispatcher*).

**Precondición de Reconstrucción (Prop. 1).** R recupera s solo si I(ĉ; s) supera un umbral
I_min(φ, ε). Cuando I(ĉ; s) → 0 la reconstrucción colapsa a muestrear del prior P(s | φ): la
salida es una *alucinación domain-plausible*, no una recuperación. La respuesta honesta en ese
régimen es el sentinel ``[UNRECOVERABLE]`` (abstención).

Tres modos con una interfaz común (``Reconstructor``), para poder MEDIR cuánto aporta cada uno:

  - **Modo A — Oracle baseline** (``OracleReconstructor``): determinista, reproducible. Tiene
    acceso a la verdad (por eso es ORACLE/baseline, NO inferencia). ``productive = False``.
  - **Modo B — LLM-GSR real** (``LLMReconstructor``): ve SOLO el OCR degradado + φ. Nunca la
    verdad. Devuelve campos, evidencia, incertidumbre y abstenciones. ``productive = True``.
  - **Modo C — Baseline conservador** (``ConservativeReconstructor``): conserva lo legible del
    OCR y ABSTIENE lo ilegible (no inventa). Mide el aporte del LLM frente a no-generar.
    ``productive = True``.

Un reconstructor ``productive`` recibe ``reconstruct(invoice_id, epoch, ocr_text, context)`` y
NO tiene forma de acceder al ground truth (ver ``tests/test_reconstructors.py``). El oracle usa
una firma compatible con el pipeline histórico y lleva la verdad en su constructor.
"""
from __future__ import annotations

import copy
import random
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .domain_context import DomainContext

# Sentinel de honestidad epistémica (Sección 4.4 del paper): el reconstructor DECLINA en vez
# de muestrear del prior cuando la precondición falla para un campo.
UNRECOVERABLE = "[UNRECOVERABLE]"

FaultHook = Callable[[str, dict[str, Any], random.Random], dict[str, Any]]


@dataclass
class ReconstructionResult:
    """Salida de un reconstructor productivo: campos + evidencia + incertidumbre + abstenciones."""
    extraction: dict[str, Any]
    mode: str
    evidence: dict[str, str] = field(default_factory=dict)       # campo -> fragmento OCR (o "")
    uncertainty: dict[str, float] = field(default_factory=dict)  # campo -> [0,1]
    abstained: list[str] = field(default_factory=list)           # campos con [UNRECOVERABLE]

    def as_extraction(self) -> dict[str, Any]:
        return self.extraction


# ── patrones de valor por tipo de campo (para el modo conservador) ───────────
_PAT = {
    "currency_amount": re.compile(r"\$?\s?-?\d{1,3}(?:\.\d{3})*,\d{2}"),
    "percentage": re.compile(r"\d{1,2}(?:,\d+)?\s?%"),
    "date": re.compile(r"\b\d{2}/\d{2}/\d{4}\b"),
    "cuit": re.compile(r"\b\d{2}-\d{8}-\d\b"),
    "identifier": re.compile(r"\b\d{3,}\b"),
}
_LINE = re.compile(
    r"(\d+)\s*[xX*]\s*(\$?\s?\d[\d.]*,\d{2})\s*=\s*(\$?\s?\d[\d.]*,\d{2})")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


# ─────────────────────────────────────────────────────────────────────────────
# Interfaz común
# ─────────────────────────────────────────────────────────────────────────────
class Reconstructor:
    """Interfaz común. Los modos productivos NO reciben la verdad."""
    productive: bool = True
    mode: str = "base"

    def reconstruct(self, invoice_id: str, epoch: int, ocr_text: str,
                    context: DomainContext) -> ReconstructionResult:  # pragma: no cover
        raise NotImplementedError


# ─────────────────────────────────────────────────────────────────────────────
# Modo A — Oracle baseline (determinista, CON acceso a la verdad: es baseline, no inferencia)
# ─────────────────────────────────────────────────────────────────────────────
class OracleReconstructor(Reconstructor):
    """Reconstrucción ORACLE determinista (``temperature=0``): emite la extracción correcta.

    ⚠️ NO es inferencia LLM: es el baseline reproducible que da el techo de calidad y permite
    aislar los experimentos de resiliencia (caídas/zombis) del ruido de reconstrucción. Lleva
    la verdad en el constructor (``truth_by_id``) o, por compatibilidad con el pipeline
    histórico, la recibe como 4º argumento posicional.

    La semilla se deriva de (base_seed, invoice_id, epoch) → un reintento repite el resultado.
    """
    productive = False
    mode = "oracle"

    def __init__(self, truth_by_id: dict[str, dict[str, Any]] | None = None,
                 base_seed: int = 0, fault_hook: FaultHook | None = None):
        self.truth_by_id = truth_by_id or {}
        self.base_seed = base_seed
        self.fault_hook = fault_hook
        self.temperature = 0.0

    def _seed_for(self, invoice_id: str, epoch: int) -> int:
        return hash((self.base_seed, invoice_id, epoch)) & 0xFFFFFFFF

    def reconstruct(self, invoice_id: str, epoch: int, ocr_text: str,
                    context: Any = None, truth: dict[str, Any] | None = None):
        """Firma dual:
          - histórica: ``reconstruct(id, epoch, ocr, truth_dict)`` (orchestrator/ray).
          - nueva:     ``reconstruct(id, epoch, ocr, context)`` con la verdad en el constructor.
        Devuelve un **dict** (compatibilidad); los modos productivos devuelven ReconstructionResult.
        """
        rng = random.Random(self._seed_for(invoice_id, epoch))
        # Resolver la verdad: 4º arg si es la extracción (histórico) o truth=, o el constructor.
        src = truth
        if src is None and isinstance(context, dict):
            src = context
        if src is None:
            src = self.truth_by_id.get(invoice_id, {})
        extraction = copy.deepcopy(src)
        if self.fault_hook is not None:
            extraction = self.fault_hook(invoice_id, extraction, rng)
        return extraction


# Alias de compatibilidad: el pipeline histórico importa ``DeterministicReconstructor``.
DeterministicReconstructor = OracleReconstructor


# ─────────────────────────────────────────────────────────────────────────────
# Modo C — Baseline conservador (parsea el OCR, ABSTIENE lo ilegible, NO inventa)
# ─────────────────────────────────────────────────────────────────────────────
class ConservativeReconstructor(Reconstructor):
    """Conserva los campos reconocibles en el OCR; abstiene (``[UNRECOVERABLE]``) los ilegibles.

    No accede a la verdad ni genera valores plausibles: mide cuánto se recupera SIN un modelo
    generativo. Para cada campo del esquema busca una línea ``etiqueta: valor`` (por nombre o
    alias) y valida el valor contra el tipo; si no ancla, abstiene.
    """
    productive = True
    mode = "conservative"

    def reconstruct(self, invoice_id: str, epoch: int, ocr_text: str,
                    context: DomainContext) -> ReconstructionResult:
        lines = ocr_text.splitlines()
        extraction: dict[str, Any] = {}
        evidence: dict[str, str] = {}
        abstained: list[str] = []

        for fspec in context.fields:
            labels = [fspec.name, *fspec.aliases]
            value, ev = self._find_field(lines, labels, fspec.type)
            if value is None:
                if not fspec.optional:
                    extraction[fspec.name] = UNRECOVERABLE
                    abstained.append(fspec.name)
            else:
                extraction[fspec.name] = value
                evidence[fspec.name] = ev

        # Líneas de detalle (para la dimensión de cálculo), solo si son legibles.
        items = self._find_lines(lines)
        calc = context.calc_keys or {}
        if items and calc:
            arr = (calc.get("lines", {}) or {}).get("array", "items")
            extraction[arr] = items
            # Espejos de cálculo que el conservador puede derivar SIN inventar:
            # subtotal = Σ importes de línea (si todas las líneas anclaron).
            try:
                sub = sum(_amount_cents(it["importe"]) for it in items)
                extraction[calc.get("subtotal", "subtotal")] = _fmt_cents(sub)
                extraction["base_imponible"] = _fmt_cents(sub)
            except Exception:
                pass
        return ReconstructionResult(extraction=extraction, mode=self.mode,
                                    evidence=evidence, abstained=abstained)

    @staticmethod
    def _find_field(lines: list[str], labels: list[str], ftype: str):
        if ftype == "identifier":
            pat = _PAT["cuit"] if any("cuit" in _norm(l) for l in labels) else _PAT["identifier"]
        else:
            pat = _PAT.get(ftype)
        label_norms = [_norm(l) for l in labels]
        for ln in lines:
            low = _norm(ln)
            for lab in label_norms:
                if low.startswith(lab + " ") or low.startswith(lab + ":") or (
                        ":" in low and low.split(":", 1)[0].strip() == lab):
                    rhs = ln.split(":", 1)[1] if ":" in ln else ln[len(lab):]
                    if pat is None:
                        val = rhs.strip()
                        return (val, ln.strip()) if val else (None, "")
                    m = pat.search(rhs)
                    if m:
                        return m.group(0).strip(), ln.strip()
        return None, ""

    @staticmethod
    def _find_lines(lines: list[str]) -> list[dict[str, str]]:
        out = []
        for ln in lines:
            m = _LINE.search(ln)
            if m:
                qty, price, amount = m.group(1), m.group(2).strip(), m.group(3).strip()
                out.append({"cantidad": qty, "precio_unitario": price, "importe": amount})
        return out


# ─────────────────────────────────────────────────────────────────────────────
# Modo B — LLM-GSR real (ve SOLO el OCR + φ; nunca la verdad)
# ─────────────────────────────────────────────────────────────────────────────
_SYSTEM = (
    "Sos un sistema de reconstrucción de facturas a partir de texto OCR DEGRADADO. "
    "Recibís texto con ruido y un esquema de campos. Tu tarea es recuperar los campos del "
    "esquema USANDO ÚNICAMENTE el texto provisto. Reglas estrictas:\n"
    "1) No inventes valores. Si un campo no se puede inferir del texto con evidencia, "
    f'devolvé exactamente "{UNRECOVERABLE}" en ese campo.\n'
    "2) Para cada campo devolvé también un fragmento EXACTO del texto que lo respalda "
    "(evidence) y una confianza 0..1 (confidence).\n"
    "3) Respetá el formato es-AR de importes ($ 1.234,56) y fechas (dd/mm/aaaa).\n"
    "4) Devolvé SOLO un objeto JSON."
)


class LLMReconstructor(Reconstructor):
    """Reconstructor real sobre un LLM (OpenAI). Determinista (``temperature=0``)."""
    productive = True
    mode = "llm"

    def __init__(self, client, *, max_chars: int = 6000):
        self.client = client
        self.max_chars = max_chars

    def reconstruct(self, invoice_id: str, epoch: int, ocr_text: str,
                    context: DomainContext) -> ReconstructionResult:
        import json
        schema = context.schema_for_prompt()
        field_names = context.field_names()
        user = (
            f"Tipo de documento: {context.doc_type}\n\n"
            f"Esquema de campos esperados:\n{schema}\n\n"
            f"Texto OCR degradado (única fuente permitida):\n---\n{ocr_text[:self.max_chars]}\n---\n\n"
            "Devolvé un JSON con esta forma: "
            '{"fields": {<nombre_campo>: {"value": <str|' + f'"{UNRECOVERABLE}"' + ">, "
            '"evidence": <str>, "confidence": <num 0..1>}}}. '
            f"Campos a devolver: {field_names}."
        )
        raw = self.client.complete_json(_SYSTEM, user)
        try:
            data = json.loads(raw)
        except Exception:
            data = {}
        fields = data.get("fields", data) if isinstance(data, dict) else {}

        extraction: dict[str, Any] = {}
        evidence: dict[str, str] = {}
        uncertainty: dict[str, float] = {}
        abstained: list[str] = []
        for name in field_names:
            cell = fields.get(name)
            if isinstance(cell, dict):
                val = cell.get("value")
                ev = str(cell.get("evidence", "") or "")
                conf = cell.get("confidence")
            else:
                val, ev, conf = cell, "", None
            if val is None or (isinstance(val, str) and val.strip() == UNRECOVERABLE):
                extraction[name] = UNRECOVERABLE
                abstained.append(name)
            else:
                extraction[name] = val if isinstance(val, str) else str(val)
                evidence[name] = ev
            if isinstance(conf, (int, float)):
                uncertainty[name] = max(0.0, min(1.0, 1.0 - float(conf)))
        # Líneas de detalle, si el modelo las devolvió.
        calc = context.calc_keys or {}
        arr_key = (calc.get("lines", {}) or {}).get("array", "items")
        arr = data.get(arr_key) if isinstance(data, dict) else None
        if isinstance(arr, list) and arr:
            extraction[arr_key] = arr
        for ck in ("subtotal", "total"):
            v = data.get(calc.get(ck, ck)) if isinstance(data, dict) else None
            if isinstance(v, (str, int, float)):
                extraction[calc.get(ck, ck)] = str(v)
        return ReconstructionResult(extraction=extraction, mode=self.mode, evidence=evidence,
                                    uncertainty=uncertainty, abstained=abstained)


# ── helpers de importes (compartidos con faults/corpus) ──────────────────────
def _amount_cents(s: str) -> int:
    neg = "-" in s
    digits = re.sub(r"[^\d]", "", s.split(",")[0]) or "0"
    cents = (s.split(",")[1] if "," in s else "00")[:2].ljust(2, "0")
    val = int(digits) * 100 + int(cents)
    return -val if neg else val


def _fmt_cents(cents: int) -> str:
    neg = cents < 0
    cents = abs(cents)
    pesos, cc = divmod(cents, 100)
    return f"{'-' if neg else ''}$ {pesos:,}".replace(",", ".") + f",{cc:02d}"
