# Autor: @Raven
"""Adapters de producción: Azure→Region, RetVec→sim_fn, NDL→Redis (con fakes offline)."""
from __future__ import annotations

from framework.vera import Source, load_contract, verify
from framework.vera.adapters import regions_from_azure, retvec_sim_fn
from framework.vera.ndl import NDL, RedisNDLStore
from framework.vera.semantic import apply_semantic

# ── Azure DI → Region ─────────────────────────────────────────────────────────

_AZURE_PAGES = [{
    "page_number": 1,
    "lines": [
        {"content": "Total",    "polygon": [0, 0, 40, 0, 40, 10, 0, 10]},
        {"content": "1.000,00", "polygon": [50, 0, 110, 0, 110, 10, 50, 10]},
        {"content": "2.500,00", "polygon": [0, 20, 60, 20, 60, 30, 0, 30]},
    ],
}]


def test_regions_from_azure_flat_polygon():
    regs = regions_from_azure(_AZURE_PAGES)
    assert len(regs) == 3
    assert regs[0].text == "Total" and regs[0].bbox == (0, 0, 40, 10) and regs[0].page == 1


def test_regions_from_azure_point_polygon():
    pages = [{"page_number": 2, "lines": [
        {"content": "IVA", "polygon": [{"x": 5, "y": 5}, {"x": 25, "y": 5},
                                        {"x": 25, "y": 15}, {"x": 5, "y": 15}]}]}]
    regs = regions_from_azure(pages)
    assert regs[0].bbox == (5, 5, 25, 15) and regs[0].page == 2


def test_azure_regions_feed_geometric_anchoring():
    contract = load_contract({"entities": [{"name": "Total", "type": "number"}]})
    regs = regions_from_azure(_AZURE_PAGES)
    # 2.500,00 está debajo de "Total" (geom) aunque 1.000,00 esté más cerca en texto.
    rep = verify({"Total": "2500"}, Source.ocr("", regions=regs), contract)
    assert rep.records[0].verdict.value == "supported"
    assert "geom" in rep.records[0].reason


# ── RetVec → sim_fn (embedder inyectado, sin cargar modelo) ───────────────────

class _FakeEmbedder:
    """One-hot por token sobre un vocab fijo → coseno determinista, sin TF."""
    _V = {"neumonia": 0, "bilateral": 1, "fractura": 2, "cadera": 3, "paciente": 4}

    def embed_texts(self, texts):
        out = []
        for t in texts:
            v = [0.0] * len(self._V)
            for tok in t.lower().split():
                if tok in self._V:
                    v[self._V[tok]] += 1.0
            out.append(v)
        return out


def test_retvec_sim_fn_with_injected_embedder():
    sim = retvec_sim_fn(embedder=_FakeEmbedder())
    contract = load_contract({"entities": [{"name": "Diagnostico", "type": "text"}]})
    ocr = "neumonia bilateral\n"
    rep = verify({"Diagnostico": "neumonia bilateral"}, Source.ocr(ocr), contract)
    rep2 = apply_semantic(rep, ocr, contract, sim_fn=sim)
    assert rep2.records[0].verdict.value == "supported"


# ── NDL sobre Redis (fake client) ─────────────────────────────────────────────

class _FakeRedis:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


def test_ndl_redis_store_persists_across_instances():
    client = _FakeRedis()
    ndl1 = NDL(store=RedisNDLStore(client))
    ndl1.record("factura", "Total", "acme sa marzo 2024 servicios", "1000")
    # otra instancia sobre el MISMO backend ve el anti-patrón (persistencia)
    ndl2 = NDL(store=RedisNDLStore(client))
    sig = ndl2.check("factura", "Total", "acme sa marzo 2024 servicios detalle", "1000")
    assert sig.matched is True and sig.value_hit is True
