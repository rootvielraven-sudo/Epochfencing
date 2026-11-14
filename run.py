# Autor: @Raven
"""CLI del trabajo final — pipeline distribuido de reconstrucción de facturas con
recuperación ante fallos y compuerta VERA.

Ejemplos:
    python run.py exp1                      # caídas de nodo (3 modos)
    python run.py exp2                      # corrupción + alucinación (con/sin VERA)
    python run.py pdf --n 12                # genera un PDF de demo (verificadas + degradadas)
    python run.py ray --n 30                # corre el pipeline sobre Ray (Pure Dispatcher)
    python run.py all                       # exp1 + exp2

Nota de entorno: si el venv hereda ROS (PYTHONPATH a /opt/ros), ejecutá con
    env -u PYTHONPATH PYTHONNOUSERSITE=1 .venv311/bin/python run.py ...
"""
from __future__ import annotations

import argparse
import json
import os


def _real_invoices(args):
    """Carga el corpus REAL de Desktop/datos-demo si se pasó --real; si no, None (sintético)."""
    if not getattr(args, "real", False):
        return None
    from pipeline.real_corpus import load_real_corpus
    inv = load_real_corpus(getattr(args, "demo_dir", None), limit=args.n or None,
                           noise_tier="degraded")
    print(f"[corpus REAL] {len(inv)} facturas de datos-demo "
          f"({sum(1 for i in inv if i['truth'].get('items'))} con cálculo)")
    return inv


def _cmd_exp1(args):
    from experiments.exp1_node_crash import run, _print
    res = run(n=args.n, crash_fraction=args.crash_fraction, invoices=_real_invoices(args))
    _print(res)
    _dump(res, args.out, "exp1.json")


def _cmd_exp2(args):
    from experiments.exp2_content import run, _print
    res = run(n=args.n, corruption_rate=args.corruption, hallucination_rate=args.hallucination,
              invoices=_real_invoices(args))
    _print(res)
    _dump(res, args.out, "exp2.json")


def _cmd_all(args):
    _cmd_exp1(args)
    _cmd_exp2(args)


def _cmd_pdf(args):
    import shutil
    from pipeline.corpus import generate_corpus
    from pipeline.faults import ContentFaultPlan
    from pipeline.gate import VeraGate
    from pipeline.orchestrator import LocalOrchestrator
    from pipeline.pdf import build_pdf
    from pipeline.reconstructor import DeterministicReconstructor

    store_dir = os.path.join(args.out, "store_pdf")
    if os.path.isdir(store_dir):
        shutil.rmtree(store_dir)
    invoices = _real_invoices(args) or generate_corpus(args.n, seed=20261112, noise_tier="degraded")
    ids = [i["invoice_id"] for i in invoices]
    calc_ids = {i["invoice_id"] for i in invoices if i["truth"].get("items")}
    plan = ContentFaultPlan(ids, corruption_rate=0.2, hallucination_rate=0.2, seed=20261112,
                            calc_ids=calc_ids or None)
    orch = LocalOrchestrator(
        invoices, mode="reclaim+epoch", gate=VeraGate(enabled=True),
        reconstructor=DeterministicReconstructor(base_seed=1, fault_hook=plan.hook),
        store_root=os.path.join(args.out, "store_pdf"))
    orch.run()
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, "facturas.pdf")
    inv_map = {i["invoice_id"]: i for i in invoices}
    counts = build_pdf(orch.store.all_records(), inv_map, path)
    print(f"PDF escrito en {path}  → {counts}  (inyectado: {plan.counts})")


def _cmd_ray(args):
    from pipeline.corpus import generate_corpus
    from pipeline.faults import CrashPlan
    from pipeline.gate import VeraGate
    from pipeline.ray_dispatcher import RayOrchestrator

    rc = _redis(args) if getattr(args, "redis", False) else None
    invoices = generate_corpus(args.n, seed=20261112, noise_tier="degraded")
    ids = [i["invoice_id"] for i in invoices]
    crash = CrashPlan(ids, crash_fraction=args.crash_fraction, crash_attempt=1, seed=7)
    orch = RayOrchestrator(
        invoices, mode=args.mode, gate=VeraGate(enabled=True),
        crash_plan=crash, store_root=os.path.join(args.out, "store_ray"),
        base_seed=1, num_workers=args.workers, redis_client=rc)
    m = orch.run()
    print("\n=== RAY (Pure Dispatcher)" + (" · store REDIS" if rc else "") + " ===")
    print(json.dumps(m.to_dict(), indent=2, ensure_ascii=False))


def _redis(args):
    from pipeline.mika_env import load_env, redis_client
    rc = redis_client(getattr(args, "env", None), required=False)
    src = load_env(getattr(args, "env", None)).get("_source_path", "?")
    if rc is None:
        print(f"[redis] no alcanzable (.env: {src}) → se usa backend en memoria")
    else:
        print(f"[redis] conectado (.env: {src})")
    return rc


def _cmd_redis(args):
    """Demo de uso real de Redis (mika .env): store con fencing + NDL persistente de VERA."""
    import shutil
    from pipeline.corpus import generate_corpus
    from pipeline.faults import ContentFaultPlan
    from pipeline.gate import VeraGate
    from pipeline.orchestrator import LocalOrchestrator
    from pipeline.reconstructor import DeterministicReconstructor
    from framework.vera.ndl import NDL, RedisNDLStore

    rc = _redis(args)
    if rc is None:
        print("Sin Redis no hay demo. Levantá el contenedor o ajustá MIKA_ENV/.env."); return

    # 1) Experimento 1 con el store de epoch fencing EN REDIS (recuperación distribuida real)
    print("\n--- Exp1 sobre store Redis (modo reclaim+epoch) ---")
    invoices = generate_corpus(args.n, seed=20261112, noise_tier="degraded")
    from pipeline.faults import CrashPlan
    ids = [i["invoice_id"] for i in invoices]
    orch = LocalOrchestrator(
        invoices, mode="reclaim+epoch", gate=VeraGate(enabled=True),
        reconstructor=DeterministicReconstructor(base_seed=1),
        crash_plan=CrashPlan(ids, crash_fraction=0.3, seed=7),
        store_root="redisdemo", redis_client=rc)
    m = orch.run()
    print(json.dumps({k: m.to_dict()[k] for k in
                      ("facturas_perdidas", "facturas_duplicadas", "reclaims",
                       "zombie_writes_rejected", "verified", "degraded")},
                     ensure_ascii=False))

    # 2) NDL persistente: dos corridas; la 2ª ya conoce los anti-patrones de la 1ª
    print("\n--- NDL de VERA persistente en Redis (aprende alucinaciones entre corridas) ---")
    ndl = NDL(store=RedisNDLStore(rc, prefix="tf:ndl:demo:"))
    plan = ContentFaultPlan(ids, corruption_rate=0.0, hallucination_rate=0.4, seed=5)
    for ronda in (1, 2):
        gate = VeraGate(enabled=True, ndl=ndl, learn=True)
        orch = LocalOrchestrator(
            invoices, mode="reclaim+epoch", gate=gate,
            reconstructor=DeterministicReconstructor(base_seed=1, fault_hook=plan.hook),
            store_root=f"ndldemo{ronda}", redis_client=rc)
        orch.run()
        n_ap = len(list(rc.scan_iter(match="tf:ndl:demo:*")))
        print(f"  ronda {ronda}: anti-patrones guardados en Redis = {n_ap}")
    print("  (la NDL queda en Redis; en una corrida futura VERA ya los reconoce)")


def _dump(obj, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    print(f"  → {os.path.join(out_dir, name)}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _add_real(p):
        p.add_argument("--real", action="store_true",
                       help="usa facturas REALES de Desktop/datos-demo (en vez de sintéticas)")
        p.add_argument("--demo-dir", default=None, dest="demo_dir",
                       help="dir del corpus real (default: /home/athenas/Desktop/datos-demo)")

    p1 = sub.add_parser("exp1", help="caídas de nodo")
    p1.add_argument("--n", type=int, default=40)
    p1.add_argument("--crash-fraction", type=float, default=0.3, dest="crash_fraction")
    p1.add_argument("--out", default="data")
    _add_real(p1)
    p1.set_defaults(func=_cmd_exp1)

    p2 = sub.add_parser("exp2", help="corrupción + alucinación")
    p2.add_argument("--n", type=int, default=60)
    p2.add_argument("--corruption", type=float, default=0.25)
    p2.add_argument("--hallucination", type=float, default=0.25)
    p2.add_argument("--out", default="data")
    _add_real(p2)
    p2.set_defaults(func=_cmd_exp2)

    pa = sub.add_parser("all", help="exp1 + exp2")
    pa.add_argument("--n", type=int, default=40)
    pa.add_argument("--crash-fraction", type=float, default=0.3, dest="crash_fraction")
    pa.add_argument("--corruption", type=float, default=0.25)
    pa.add_argument("--hallucination", type=float, default=0.25)
    pa.add_argument("--out", default="data")
    _add_real(pa)
    pa.set_defaults(func=_cmd_all)

    pp = sub.add_parser("pdf", help="genera un PDF de demo")
    pp.add_argument("--n", type=int, default=12)
    pp.add_argument("--out", default="data")
    _add_real(pp)
    pp.set_defaults(func=_cmd_pdf)

    pr = sub.add_parser("ray", help="corre sobre Ray")
    pr.add_argument("--n", type=int, default=30)
    pr.add_argument("--mode", default="reclaim+epoch")
    pr.add_argument("--crash-fraction", type=float, default=0.3, dest="crash_fraction")
    pr.add_argument("--workers", type=int, default=4)
    pr.add_argument("--redis", action="store_true", help="store con epoch fencing en Redis")
    pr.add_argument("--env", default=None, help="ruta a un .env (default: el de mika)")
    pr.add_argument("--out", default="data")
    pr.set_defaults(func=_cmd_ray)

    pd = sub.add_parser("redis", help="demo: store Redis + NDL persistente (usa el .env de mika)")
    pd.add_argument("--n", type=int, default=30)
    pd.add_argument("--env", default=None, help="ruta a un .env (default: el de mika)")
    pd.set_defaults(func=_cmd_redis)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
