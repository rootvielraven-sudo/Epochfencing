# Autor: @Raven
"""Tests de los tres modos de reconstrucción y de la NO-FUGA de ground truth (sección 9).

Regla auditada: las funciones PRODUCTIVAS (Modo B LLM y Modo C conservador) no pueden acceder
a la verdad; reconstruyen solo desde el OCR + contexto de dominio. El Modo A (oracle) es un
baseline explícito y SÍ tiene la verdad (por eso ``productive = False``).
"""
from __future__ import annotations

import inspect
import os

import pytest

from pipeline.corpus import generate_corpus
from pipeline.domain_context import DomainContext
from pipeline.reconstructor import (
    UNRECOVERABLE,
    ConservativeReconstructor,
    LLMReconstructor,
    OracleReconstructor,
    ReconstructionResult,
)

CTX = DomainContext.from_contract("factura", client="default", config_root="config")


# ── separación de ground truth ───────────────────────────────────────────────
def test_oracle_es_baseline_no_productivo():
    assert OracleReconstructor().productive is False
    assert OracleReconstructor().mode == "oracle"


@pytest.mark.parametrize("cls", [ConservativeReconstructor, LLMReconstructor])
def test_modos_productivos_no_reciben_verdad(cls):
    """La firma de ``reconstruct`` de los modos productivos NO tiene parámetro de verdad."""
    assert cls.productive is True
    params = set(inspect.signature(cls.reconstruct).parameters)
    assert "truth" not in params and "truth_by_id" not in params
    assert params == {"self", "invoice_id", "epoch", "ocr_text", "context"}
    # Y su __init__ no admite inyectar la verdad.
    init_params = set(inspect.signature(cls.__init__).parameters)
    assert "truth" not in init_params and "truth_by_id" not in init_params


def test_conservador_solo_usa_el_ocr_no_la_verdad():
    """El OCR dice un total; la 'verdad' dice otro. El conservador debe devolver el del OCR."""
    inv = generate_corpus(1, seed=3, noise_tier=None)[0]
    # Contaminamos la verdad con un valor imposible; si el conservador la mirara, lo delataría.
    inv["truth"]["Total a pagar"] = "$ 1,00"
    rec = ConservativeReconstructor()
    res = rec.reconstruct(inv["invoice_id"], 1, inv["ocr_text"], CTX)
    assert isinstance(res, ReconstructionResult)
    # El valor viene del OCR (que NO fue tocado), no de la verdad contaminada.
    assert res.extraction.get("Total a pagar") not in ("$ 1,00", None)
    assert "Total a pagar" in inv["ocr_text"]


# ── comportamiento del modo conservador ──────────────────────────────────────
def test_conservador_recupera_campos_legibles():
    inv = generate_corpus(1, seed=5, noise_tier=None)[0]
    res = ConservativeReconstructor().reconstruct(inv["invoice_id"], 1, inv["ocr_text"], CTX)
    # Campos con etiqueta clara y valor bien formado → recuperados y con evidencia.
    for campo in ("Total a pagar", "CUIT emisor", "Fecha de emisión", "Subtotal"):
        assert res.extraction.get(campo) not in (None, UNRECOVERABLE), campo
        assert campo in res.evidence


def test_conservador_abstiene_lo_ilegible_no_inventa():
    """Si un campo requerido no aparece reconocible en el OCR, se abstiene ([UNRECOVERABLE]),
    NO inventa un valor plausible."""
    inv = generate_corpus(1, seed=7, noise_tier=None)[0]
    # Quitamos la línea del CUIT del OCR.
    ocr_sin_cuit = "\n".join(l for l in inv["ocr_text"].splitlines()
                             if "CUIT" not in l)
    res = ConservativeReconstructor().reconstruct(inv["invoice_id"], 1, ocr_sin_cuit, CTX)
    assert res.extraction.get("CUIT emisor") == UNRECOVERABLE
    assert "CUIT emisor" in res.abstained


# ── modo B: parsing del LLM sin red (cliente simulado) ────────────────────────
class _FakeClient:
    """Cliente LLM falso: devuelve un JSON fijo, sin red (prueba el parseo del Modo B)."""
    def __init__(self, payload: str):
        self.payload = payload

    def complete_json(self, system: str, user: str) -> str:
        return self.payload


def test_llm_parsea_campos_evidencia_y_abstencion():
    payload = (
        '{"fields": {'
        '"Total a pagar": {"value": "$ 1.210,00", "evidence": "Total a pagar: $ 1.210,00", "confidence": 0.9},'
        f'"CUIT emisor": {{"value": "{UNRECOVERABLE}", "evidence": "", "confidence": 0.1}}'
        '}}'
    )
    rec = LLMReconstructor(_FakeClient(payload))
    res = rec.reconstruct("F1", 1, "ocr...", CTX)
    assert res.extraction["Total a pagar"] == "$ 1.210,00"
    assert res.evidence["Total a pagar"].startswith("Total a pagar")
    assert res.extraction["CUIT emisor"] == UNRECOVERABLE
    assert "CUIT emisor" in res.abstained
    # incertidumbre = 1 - confianza
    assert abs(res.uncertainty["Total a pagar"] - 0.1) < 1e-6


# ── modo B en vivo: solo si el usuario lo pide explícitamente (gasta tokens) ──
@pytest.mark.skipif(os.environ.get("TF_LLM_LIVE") != "1",
                    reason="requiere TF_LLM_LIVE=1 (hace llamada real a OpenAI, gasta tokens)")
def test_llm_vivo_ping():
    from pipeline.llm_client import LLMClient
    client = LLMClient.from_config()
    assert client is not None and client.available()
    ok, info = client.ping()
    assert ok, info
