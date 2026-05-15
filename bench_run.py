"""
bench_run.py — Runs the official Anvil P-02 harness from the project root.

This sets sys.path correctly so `from engine.x import ...` works inside
the bench's own adapter, then delegates to the harness.

Usage:
    python bench_run.py --seeds 9999 31415 27182 16180 11235 --out report.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

# Add the root directory AND the bench directory to the path so that both
# `engine.*` (from root) and the bench local modules resolve.
ROOT = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(ROOT, "Anvil-P-E", "bench-p02-context")
sys.path.insert(0, ROOT)
sys.path.insert(0, BENCH)  # BENCH first so its local modules take priority

from generator import GenConfig  # bench dataset generator
from harness import run  # bench harness


def adapter_factory():
    from adapters.engine import Engine  # bench's own adapter (uses engine.*)

    return Engine()


def main():
    ap = argparse.ArgumentParser(description="Official P-02 benchmark via bench_run.py")
    ap.add_argument(
        "--seeds", type=int, nargs="+", default=[9999, 31415, 27182, 16180, 11235]
    )
    ap.add_argument("--mode", choices=["fast", "deep"], default="fast")
    ap.add_argument("--n-services", type=int, default=12)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=2)
    ap.add_argument("--out", default="bench_report.json")
    args = ap.parse_args()

    cfg = GenConfig(seed=args.seeds[0], n_services=args.n_services, days=args.days)
    report = run(
        adapter_factory, cfg=cfg, mode=args.mode, seeds=args.seeds, warmup=args.warmup
    )

    with open(args.out, "w") as f:
        json.dump(report, f, indent=2, default=str)

    agg = report["aggregated"]
    sc = report["score"]

    print()
    print("=" * 65)
    print("  OFFICIAL P-02 BENCHMARK RESULTS")
    print("=" * 65)
    print(f"  recall@5:         {agg['recall@5']:.4f}   (target ≥ 0.65)")
    print(f"  precision@5_mean: {agg['precision@5_mean']:.4f}   (target ≥ 0.40)")
    print(f"  remediation_acc:  {agg['remediation_acc']:.4f}   (target ≥ 0.80)")
    print(f"  latency_p95_ms:   {agg['latency_p95_ms']:.2f}    (target ≤ 2000)")
    print(f"  weighted_score:   {sc['weighted_score']:.4f} / 0.80")
    print("=" * 65)
    print()
    print("  PER-SEED DETAIL:")
    for s in report["per_seed"]:
        sm = s["summary"]
        flag = (
            "✅" if sm["recall@5"] >= 0.65 and sm["precision@5_mean"] >= 0.40 else "⚠️ "
        )
        print(
            f"  {flag} seed {s['seed']:5d} → "
            f"recall={sm['recall@5']:.2f}  "
            f"precision={sm['precision@5_mean']:.2f}  "
            f"remed={sm['remediation_acc']:.2f}  "
            f"lat_p95={sm['latency_p95_ms']:.2f}ms"
        )
    print("=" * 65)
    print(f"  Report written → {args.out}")
    print()


if __name__ == "__main__":
    main()
