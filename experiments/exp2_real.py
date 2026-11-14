# Autor: @Raven
"""Experimento 2 (REAL) — Calidad de reconstrucción: Oracle vs Conservador vs LLM-GSR.

Mide cuánto RECUPERA cada modo del contenido degradado por OCR, a varios grados de degradación.
La comparación contra la verdad (gold) ocurre EXCLUSIVAMENTE en este evaluador (sección 9); los
reconstructores productivos (conservador/LLM) solo vieron el OCR + el contexto de dominio.

Por (modo, grado de ruido) reporta:
  - exactitud por campo, exactitud cuando responde (excluye abstenciones), factura completa exacta
  - tasa de alucinación (responde y se equivoca), tasa de abstención ([UNRECOVERABLE])
  - error numérico (campos monetarios equivocados), distancia de edición media
  - latencia media; tokens (solo LLM, coste medible)

El Modo B (LLM) gasta tokens de OpenAI: por eso el corpus es chico y configurable.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from framework.vera.calc_contract import amount_to_cents
from pipeline.corpus import generate_corpus
from pipeline.domain_context import DomainContext
from pipeline.llm_client import LLMClient
from pipeline.reconstructor import (
    UNRECOVERABLE,
    ConservativeReconstructor,
    LLMReconstructor,
    OracleReconstructor,
)

_SCORED = ["Comprobante", "CUIT emisor", "Fecha de emisión",
           "Total a pagar", "Subtotal", "Importe IVA", "Alícuota IVA"]
_NUMERIC = {"Total a pagar", "Subtotal", "Importe IVA"}


def _norm(val: str | None, ftype: str) -> str | None:
    if val is None:
        return None
    s = str(val).strip()
    if ftype == "currency_amount":
        c = amount_to_cents(s)
        return str(int(c)) if c is not None else s.lower()
    if ftype == "percentage":
        return s.replace("%", "").replace(",", ".").strip()
    if ftype == "date":
        import re
        digs = re.findall(r"\d+", s)
        return "/".join(d.zfill(2) if len(d) < 4 else d for d in digs) if digs else s
    import re
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _lev(a: str, b: str) -> int:
    a, b = a or "", b or ""
    if a == b:
        return 0
    m, n = len(a), len(b)
    prev = list(range(n + 1))
    for i in range(1, m + 1):
        cur = [i] + [0] * n
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[n]


def _score(extraction: dict, truth: dict, ctx: DomainContext) -> dict[str, Any]:
    tmap = {f.name: f.type for f in ctx.fields}
    correct = wrong = abstained = 0
    num_total = num_wrong = 0
    dist_sum = 0.0
    all_ok = True
    for name in _SCORED:
        ftype = tmap.get(name, "text")
        rv = extraction.get(name)
        tv = truth.get(name)
        is_num = name in _NUMERIC
        num_total += 1 if is_num else 0
        if isinstance(rv, str) and rv.strip() == UNRECOVERABLE:
            abstained += 1
            all_ok = False
            dist_sum += 1.0
            continue
        rn, tn = _norm(rv, ftype), _norm(tv, ftype)
        if rn is not None and rn == tn:
            correct += 1
        else:
            wrong += 1
            all_ok = False
            if is_num:
                num_wrong += 1
        a, b = str(rv or ""), str(tv or "")
        dist_sum += (_lev(a, b) / max(len(a), len(b), 1))
    return {"correct": correct, "wrong": wrong, "abstained": abstained,
            "num_total": num_total, "num_wrong": num_wrong,
            "dist": dist_sum, "full_ok": all_ok, "n": len(_SCORED)}


def run(n: int = 12, tiers: tuple[str, ...] = ("clean", "degraded", "poor"),
        seed: int = 20261113, out_dir: str = "data/exp2real",
        use_llm: bool = True, client: "LLMClient | None" = None) -> dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    ctx = DomainContext.from_contract("factura")
    if use_llm and client is None:
        client = LLMClient.from_config()
    llm_ok = bool(use_llm and client and client.available())

    out: dict[str, Any] = {"n": n, "tiers": list(tiers), "llm": llm_ok,
                           "model": client.cfg.model if llm_ok else None, "por_tier": {}}

    for tier in tiers:
        invs = generate_corpus(n, seed=seed, noise_tier=tier)
        truth_by_id = {i["invoice_id"]: i["truth"] for i in invs}
        modes = {
            "oracle": OracleReconstructor(truth_by_id),
            "conservador": ConservativeReconstructor(),
        }
        if llm_ok:
            modes["llm"] = LLMReconstructor(client)

        tier_res: dict[str, Any] = {}
        for mode_name, rec in modes.items():
            agg = {"correct": 0, "wrong": 0, "abstained": 0, "num_total": 0,
                   "num_wrong": 0, "dist": 0.0, "full_ok": 0, "n": 0}
            t0 = time.perf_counter()
            tok0 = (client.prompt_tokens + client.completion_tokens) if (llm_ok and mode_name == "llm") else 0
            for inv in invs:
                res = rec.reconstruct(inv["invoice_id"], 1, inv["ocr_text"], ctx)
                ext = res if isinstance(res, dict) else res.extraction
                s = _score(ext, inv["truth"], ctx)
                for k in ("correct", "wrong", "abstained", "num_total", "num_wrong", "n"):
                    agg[k] += s[k]
                agg["dist"] += s["dist"]
                agg["full_ok"] += 1 if s["full_ok"] else 0
            dt = time.perf_counter() - t0
            answered = agg["correct"] + agg["wrong"]
            nf = agg["n"]
            row = {
                "exactitud_campo": round(agg["correct"] / nf, 3) if nf else None,
                "exactitud_cuando_responde": round(agg["correct"] / answered, 3) if answered else None,
                "factura_completa_exacta": round(agg["full_ok"] / n, 3),
                "tasa_alucinacion": round(agg["wrong"] / nf, 3) if nf else None,
                "tasa_abstencion": round(agg["abstained"] / nf, 3) if nf else None,
                "error_numerico": round(agg["num_wrong"] / agg["num_total"], 3) if agg["num_total"] else None,
                "distancia_edicion_media": round(agg["dist"] / nf, 3) if nf else None,
                "latencia_ms_media": round(dt * 1000 / n, 1),
            }
            if llm_ok and mode_name == "llm":
                row["tokens"] = (client.prompt_tokens + client.completion_tokens) - tok0
            tier_res[mode_name] = row
        out["por_tier"][tier] = tier_res

    if llm_ok:
        out["tokens_totales"] = {"prompt": client.prompt_tokens,
                                 "completion": client.completion_tokens,
                                 "llamadas": client.calls}
    with open(os.path.join(out_dir, "exp2real.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


def _print(res: dict[str, Any]) -> None:
    print("\n=== EXPERIMENTO 2-REAL — CALIDAD DE RECONSTRUCCIÓN ===")
    print(f"n por tier={res['n']}  LLM={res['llm']} ({res['model']})")
    cols = ["exactitud_campo", "exactitud_cuando_responde", "factura_completa_exacta",
            "tasa_alucinacion", "tasa_abstencion", "error_numerico",
            "distancia_edicion_media", "latencia_ms_media"]
    short = ["exac", "exac|resp", "fact.exac", "alucin", "absten", "err.num", "dist.ed", "lat.ms"]
    for tier, modes in res["por_tier"].items():
        print(f"\n--- grado OCR: {tier} ---")
        print(f"{'modo':<13}" + "".join(f"{s:>12}" for s in short) + f"{'tokens':>9}")
        for mode, d in modes.items():
            line = f"{mode:<13}" + "".join(f"{str(d[c]):>12}" for c in cols)
            line += f"{str(d.get('tokens','-')):>9}"
            print(line)
    if "tokens_totales" in res:
        print("\ntokens totales:", res["tokens_totales"])
    print()


if __name__ == "__main__":
    _print(run())
