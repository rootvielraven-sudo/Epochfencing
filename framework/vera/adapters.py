# Autor: @Raven
"""VERA — adapters de PRODUCCIÓN: geometría OCR (Azure) y embedder (RetVec) para T1.

Cierran los wirings que faltaban para operar "como el paper" en producción:
  - ``regions_from_azure`` : convierte las líneas con polígono de Azure Document Intelligence
    en ``Region`` (bbox) → habilita el anclaje MIXTO geométrico. (Azure ya produce la
    geometría; falta surfacearla — ``AzureOCRWorker`` hoy la descarta.)
  - ``retvec_sim_fn``      : construye la ``sim_fn`` coseno de T1 sobre el embedder RETVec
    LOCAL de MIKA (sin API key). Perezoso: solo carga el modelo al primer uso.
"""
from __future__ import annotations

from typing import Any, Callable

from .semantic import cosine_sim_fn
from .source import Region


# ── Geometría: Azure Document Intelligence → Region ───────────────────────────

def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _polygon_to_bbox(poly: Any) -> tuple[float, float, float, float] | None:
    """Acepta polígono Azure como lista plana [x1,y1,x2,y2,...] o lista de puntos {x,y}/(x,y)."""
    if not poly:
        return None
    xs: list[float] = []
    ys: list[float] = []
    if isinstance(poly[0], (int, float)):
        it = list(poly)
        xs = [float(v) for v in it[0::2]]
        ys = [float(v) for v in it[1::2]]
    else:
        for p in poly:
            xs.append(float(_attr(p, "x", p[0] if not isinstance(p, dict) else 0)))
            ys.append(float(_attr(p, "y", p[1] if not isinstance(p, dict) else 0)))
    if not xs or not ys:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def regions_from_azure(pages: Any) -> list[Region]:
    """Convierte páginas de Azure DI (cada una con ``lines`` que tienen ``content`` y
    ``polygon``) en una lista de ``Region``. Robusto a objetos del SDK o dicts.

    Uso en producción (surfacear lo que ``AzureOCRWorker`` hoy descarta):
        result = poller.result()                 # AnalyzeResult de Azure DI
        regions = regions_from_azure(result.pages)
        report = verify(output, Source.ocr(text, regions=regions), contract)
    """
    regions: list[Region] = []
    for page in pages or []:
        page_no = int(_attr(page, "page_number", _attr(page, "page", 1)) or 1)
        for line in _attr(page, "lines", []) or []:
            text = _attr(line, "content", _attr(line, "text", "")) or ""
            bbox = _polygon_to_bbox(_attr(line, "polygon", _attr(line, "bounding_box")))
            if text.strip() and bbox is not None:
                regions.append(Region(text=text, bbox=bbox, page=page_no))
    return regions


# ── T1: sim_fn coseno sobre el embedder RETVec local de MIKA ───────────────────

def retvec_sim_fn(embedder: Any = None) -> Callable[[str, str], float]:
    """Construye la ``sim_fn`` de T1 con el embedder RETVec local de MIKA (sin API key).
    Perezoso: si no se pasa ``embedder``, lo instancia al primer uso.
    """
    holder: dict[str, Any] = {"emb": embedder}

    def _embed(text: str) -> list[float]:
        if holder["emb"] is None:
            from framework.Embeddings.ProductionRETVecEmbedder import ProductionRETVecEmbedder
            holder["emb"] = ProductionRETVecEmbedder()
        vecs = holder["emb"].embed_texts([text])
        return list(vecs[0]) if vecs else []

    return cosine_sim_fn(_embed)
