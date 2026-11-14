# Autor: @Raven
"""VERA — harness de evaluación determinista.

Dado un conjunto de casos ETIQUETADOS (cada campo del output marcado como
'correct' o 'hallucinated' según la verdad conocida), corre VERA y computa:

  - Por tipo de documento y por tipo de entidad: TP/FP/FN/TN, precision, recall
    (= capture), F1, FP-rate, human-review-rate, con N por grupo e IC Wilson 95%.

Definiciones (VERA "marca" un campo si su veredicto NO es SUPPORTED):
  - flagged      = veredicto ∈ {INVENTED, REVIEW}   (UNVERIFIED = null, se excluye)
  - truly_wrong  = gold del campo == 'hallucinated'
  - TP: flagged ∧ truly_wrong   · FP: flagged ∧ ¬truly_wrong
  - FN: ¬flagged ∧ truly_wrong  · TN: ¬flagged ∧ ¬truly_wrong

REGLA: los números salen SOLO de los casos que se pasen. Sin casos anotados no hay
métricas. Este módulo no contiene datos.
"""
from __future__ import annotations

import csv
import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from ..engine import verify
from ..source import Source
from ..vera_schema import EntitySpec
from ..vera_types import EntityVerdict

_FLAGGED = {EntityVerdict.INVENTED, EntityVerdict.REVIEW}


@dataclass(frozen=True)
class EvalCase:
    """Un caso etiquetado. ``gold`` mapea NOMBRE de entidad → 'correct'|'hallucinated'.
    Los campos sin gold se excluyen de la métrica (p.ej. null/UNVERIFIED no evaluados)."""
    doc_type: str
    source: Source
    output: dict[str, str | None]       # claves-slug del contrato
    gold: dict[str, str]                # entity_name → 'correct' | 'hallucinated'


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Intervalo de Wilson 95% para una proporción k/n. (0,0) si n==0."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


@dataclass
class _Cell:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    review: int = 0     # veredictos REVIEW (para human-review-rate)
    total: int = 0

    def metrics(self) -> dict[str, Any]:
        tp, fp, fn, tn, n = self.tp, self.fp, self.fn, self.tn, self.total
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0    # = capture
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        fpr = fp / (fp + tn) if (fp + tn) else 0.0
        cap_lo, cap_hi = wilson_ci(tp, tp + fn)
        fpr_lo, fpr_hi = wilson_ci(fp, fp + tn)
        return {
            "N": n, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": round(prec, 4), "recall_capture": round(rec, 4),
            "f1": round(f1, 4), "fp_rate": round(fpr, 4),
            "capture_ci95_lo": round(cap_lo, 4), "capture_ci95_hi": round(cap_hi, 4),
            "fp_rate_ci95_lo": round(fpr_lo, 4), "fp_rate_ci95_hi": round(fpr_hi, 4),
            "human_review_rate": round(self.review / n, 4) if n else 0.0,
        }


def evaluate(
    cases: list[EvalCase],
    contracts_by_type: dict[str, list[EntitySpec]],
) -> dict[str, Any]:
    """Corre VERA sobre los casos y agrega métricas por doc_type y por entity_type."""
    by_doc: dict[str, _Cell] = defaultdict(_Cell)
    by_ent: dict[str, _Cell] = defaultdict(_Cell)
    overall = _Cell()

    for case in cases:
        contract = contracts_by_type[case.doc_type]
        rep = verify(case.output, case.source, contract)
        rec_by_name = {r.entity_name: r for r in rep.records}
        for spec in contract:
            gold = case.gold.get(spec.name)
            if gold not in ("correct", "hallucinated"):
                continue   # sin etiqueta → fuera de la métrica
            r = rec_by_name[spec.name]
            flagged = r.verdict in _FLAGGED
            wrong = gold == "hallucinated"
            for cell in (by_doc[case.doc_type], by_ent[spec.type.value], overall):
                cell.total += 1
                if r.verdict is EntityVerdict.REVIEW:
                    cell.review += 1
                if flagged and wrong:
                    cell.tp += 1
                elif flagged and not wrong:
                    cell.fp += 1
                elif not flagged and wrong:
                    cell.fn += 1
                else:
                    cell.tn += 1

    return {
        "by_doc_type": {k: v.metrics() for k, v in sorted(by_doc.items())},
        "by_entity_type": {k: v.metrics() for k, v in sorted(by_ent.items())},
        "overall": overall.metrics(),
    }


def write_csvs(metrics: dict[str, Any], out_dir: str) -> list[str]:
    """Escribe confusion_matrix_by_doctype.csv y _by_entitytype.csv + overall.csv."""
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for group_key, fname in (("by_doc_type", "confusion_matrix_by_doctype.csv"),
                             ("by_entity_type", "confusion_matrix_by_entitytype.csv")):
        rows = metrics[group_key]
        path = os.path.join(out_dir, fname)
        cols = ["group", "N", "TP", "FP", "FN", "TN", "precision", "recall_capture",
                "f1", "fp_rate", "capture_ci95_lo", "capture_ci95_hi",
                "fp_rate_ci95_lo", "fp_rate_ci95_hi", "human_review_rate"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=cols)
            w.writeheader()
            for g, m in rows.items():
                w.writerow({"group": g, **m})
        written.append(path)
    # overall
    path = os.path.join(out_dir, "overall.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        m = metrics["overall"]
        w = csv.DictWriter(fh, fieldnames=list(m.keys()))
        w.writeheader()
        w.writerow(m)
    written.append(path)
    return written
