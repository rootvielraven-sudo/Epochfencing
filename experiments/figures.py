# Autor: @Raven
"""Figuras para el informe a partir de los CSV de ``experiments.measure``.

Paleta categórica validada (dataviz skill, slots all-pairs OK en claro):
  blue #2a78d6 · orange #eb6834 · aqua #1baf7a.
Superficie clara, tinta recesiva, etiquetas de valor directas (regla de relieve), leyenda.

Correr (después de measure):
  env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv311/bin/python -m experiments.figures
"""
from __future__ import annotations

import csv
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = "data/measure"
FIGS = "data/figuras"

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK2, GRID = "#0b0b0b", "#52514e", "#dcdcd8"
MODE_COLOR = {"none": ORANGE, "reclaim": BLUE, "reclaim+epoch": AQUA}
MODE_LABEL = {"none": "sin recuperación", "reclaim": "reclamación",
              "reclaim+epoch": "reclamación + epoch"}

plt.rcParams.update({
    "figure.facecolor": "white", "axes.facecolor": "white",
    "axes.edgecolor": INK2, "axes.labelcolor": INK, "text.color": INK,
    "xtick.color": INK2, "ytick.color": INK2, "font.size": 11,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.8,
    "axes.axisbelow": True,
})


def _read(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fig_exp1(summary: list[dict]) -> None:
    """Dos paneles: facturas perdidas y duplicadas vs fracción de caídas, por modo."""
    cfs = sorted({float(r["crash_fraction"]) for r in summary})
    modes = ["none", "reclaim", "reclaim+epoch"]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2))

    for ax, metric, title in [
        (ax1, "facturas_perdidas_mean", "Facturas perdidas"),
        (ax2, "facturas_duplicadas_mean", "Facturas duplicadas"),
    ]:
        for mode in modes:
            ys = [next(float(r[metric]) for r in summary
                       if r["mode"] == mode and float(r["crash_fraction"]) == cf)
                  for cf in cfs]
            ax.plot([int(c * 100) for c in cfs], ys, "-o", color=MODE_COLOR[mode],
                    linewidth=2, markersize=7, label=MODE_LABEL[mode])
            # etiqueta directa del valor final (no en todos los puntos)
            if ys[-1] > 0 or metric.startswith("facturas_perdidas") and mode == "none":
                ax.annotate(f"{ys[-1]:.0f}", (int(cfs[-1] * 100), ys[-1]),
                            textcoords="offset points", xytext=(6, 0),
                            color=MODE_COLOR[mode], fontsize=10, va="center")
        ax.set_title(title, color=INK, fontsize=12, pad=8)
        ax.set_xlabel("fracción de workers caídos (%)", color=INK2)
        ax.set_ylabel("facturas (media, n=50)", color=INK2)
        ax.set_ylim(bottom=-0.5)
    ax1.legend(frameon=False, fontsize=9.5, loc="upper left")
    fig.suptitle("Exp. 1 — Recuperación ante caídas de nodo", fontsize=13, color=INK, y=1.0)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "exp1_recuperacion.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_exp2(summary: list[dict]) -> None:
    """Barras: tasa de detección por tipo de fallo, con vs sin VERA, por corpus."""
    corpora = sorted({r["corpus"] for r in summary})
    fig, axes = plt.subplots(1, len(corpora), figsize=(5.2 * len(corpora), 4.2), squeeze=False)
    groups = ["det_corruption_mean", "det_hallucination_mean"]
    glabel = ["corrupción\n(cálculo)", "alucinación\n(cobertura)"]
    x = range(len(groups))
    w = 0.38

    handles = None
    for ci, corpus in enumerate(corpora):
        ax = axes[0][ci]
        local_handles = []
        for k, (cond, color, off) in enumerate([("con_vera", BLUE, -w / 2),
                                                ("sin_vera", ORANGE, w / 2)]):
            vals = [next(float(r[m]) for r in summary
                         if r["corpus"] == corpus and r["condicion"] == cond) for m in groups]
            bars = ax.bar([xi + off for xi in x], vals, w, color=color,
                          label="con VERA" if cond == "con_vera" else "sin VERA",
                          edgecolor="white", linewidth=2)
            local_handles.append(bars)
            for b, v in zip(bars, vals):
                ax.annotate(f"{v:.0%}", (b.get_x() + b.get_width() / 2, v),
                            textcoords="offset points", xytext=(0, 3),
                            ha="center", color=INK, fontsize=10)
        handles = local_handles
        ax.set_xticks(list(x)); ax.set_xticklabels(glabel)
        ax.set_ylim(0, 1.18); ax.set_ylabel("tasa de detección", color=INK2)
        ax.set_title(f"corpus {corpus}", color=INK, fontsize=12, pad=8)
    # leyenda a nivel de figura, arriba, sin chocar con las barras
    fig.legend(handles, ["con VERA", "sin VERA"], frameon=False, fontsize=10,
               loc="upper center", ncol=2, bbox_to_anchor=(0.5, 0.945))
    fig.suptitle("Exp. 2 — Detección de corrupción y alucinación (VERA)",
                 fontsize=13, color=INK, y=1.02)
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    fig.savefig(os.path.join(FIGS, "exp2_deteccion.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


def fig_exp2_fp_fn(raw: list[dict]) -> None:
    """Barras: falsos negativos con vs sin VERA (muestra el costo de no verificar)."""
    corpora = sorted({r["corpus"] for r in raw})
    import statistics as stx
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    x = range(len(corpora)); w = 0.38
    for cond, color, off, lab in [("con_vera", BLUE, -w / 2, "con VERA"),
                                  ("sin_vera", ORANGE, w / 2, "sin VERA")]:
        vals = [stx.mean(float(r["falsos_negativos"]) for r in raw
                         if r["corpus"] == c and r["condicion"] == cond) for c in corpora]
        bars = ax.bar([xi + off for xi in x], vals, w, color=color, label=lab,
                      edgecolor="white", linewidth=2)
        for b, v in zip(bars, vals):
            ax.annotate(f"{v:.1f}", (b.get_x() + b.get_width() / 2, v),
                        textcoords="offset points", xytext=(0, 3), ha="center",
                        color=INK, fontsize=10)
    ax.set_xticks(list(x)); ax.set_xticklabels([f"corpus {c}" for c in corpora])
    ax.set_ylabel("facturas corruptas que entran al PDF\n(falsos negativos, media)", color=INK2)
    ax.set_title("Exp. 2 — Costo de no verificar", fontsize=13, color=INK, pad=8)
    ax.legend(frameon=False, fontsize=9.5)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGS, "exp2_falsos_negativos.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)


def main() -> None:
    os.makedirs(FIGS, exist_ok=True)
    fig_exp1(_read(os.path.join(OUT, "exp1_summary.csv")))
    e2s = _read(os.path.join(OUT, "exp2_summary.csv"))
    fig_exp2(e2s)
    fig_exp2_fp_fn(_read(os.path.join(OUT, "exp2_raw.csv")))
    print(f"figuras escritas en {FIGS}/: exp1_recuperacion.png, exp2_deteccion.png, "
          f"exp2_falsos_negativos.png")


if __name__ == "__main__":
    main()
