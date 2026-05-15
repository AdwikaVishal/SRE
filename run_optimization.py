#!/usr/bin/env python3
"""
run_optimization.py — CLI entry point for optimization framework.

Complete command-line interface for running optimization experiments.
Supports multiple modes: full pipeline, weight sweep, ablation, targeted, validation.

Usage:
    python run_optimization.py --mode full --seeds 9999 31415 27182 --output results/
    python run_optimization.py --mode sweep --param stageA_min_similarity 0.50 0.60 0.70
    python run_optimization.py --mode ablation --baseline config.json --variants config1.json config2.json
    python run_optimization.py --mode targeted --metric precision@5_mean --initial config.json
    python run_optimization.py --mode validate --config best_config.json --seeds 9999 31415

Examples:
    # Run full optimization pipeline with default seeds
    python run_optimization.py --mode full --output results/exp1/

    # Quick weight sweep on two parameters
    python run_optimization.py \\
        --mode sweep \\
        --param stageA_min_similarity 0.50 0.55 0.60 0.65 0.70 \\
        --param decoy_similarity_cap 0.35 0.40 0.45 0.50 \\
        --seeds 9999 31415 \\
        --output results/sweep1/

    # Targeted optimization for precision
    python run_optimization.py \\
        --mode targeted \\
        --metric precision@5_mean \\
        --initial best_config.json \\
        --output results/precision_opt/

    # Ablation study comparing configs
    python run_optimization.py \\
        --mode ablation \\
        --baseline baseline_config.json \\
        --variants variant1.json variant2.json variant3.json \\
        --seeds 9999 31415 27182
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

from ablation_study_framework import AblationStudyFramework
from config_manager import ConfigManager
from experiment_runner import ExperimentRunner
from optimized_bench_runner import OptimizedBenchmarkRunner
from seed_wise_comparison import SeedWiseComparator


def parse_arguments():
    """Parse command-line arguments."""
    ap = argparse.ArgumentParser(
        description="Optimization framework for P-02 benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    ap.add_argument(
        "--mode",
        required=True,
        choices=["full", "sweep", "ablation", "targeted", "validate"],
        help="Optimization mode to run",
    )

    ap.add_argument(
        "--output",
        type=str,
        default="results/",
        help="Output directory for results (default: results/)",
    )

    ap.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[9999, 31415, 27182, 16180, 11235],
        help="Benchmark seeds to use (default: 9999 31415 27182 16180 11235)",
    )

    ap.add_argument(
        "--mode-fast",
        action="store_true",
        default=False,
        help="Use fast mode instead of deep mode (latency budget 2s vs 6s)",
    )

    # For sweep mode
    ap.add_argument(
        "--param",
        nargs="+",
        action="append",
        dest="params",
        help="Parameter sweep specification. Format: --param NAME value1 value2 value3 ...",
    )

    # For ablation mode
    ap.add_argument("--baseline", type=str, help="Baseline config file (JSON)")

    ap.add_argument(
        "--variants", type=str, nargs="+", help="Variant config files to compare (JSON)"
    )

    # For targeted mode
    ap.add_argument(
        "--metric",
        type=str,
        choices=["recall@5", "precision@5_mean", "remediation_acc", "weighted_score"],
        help="Metric to optimize for in targeted mode",
    )

    ap.add_argument(
        "--initial",
        type=str,
        help="Initial config file for targeted optimization (JSON)",
    )

    # For validate mode
    ap.add_argument("--config", type=str, help="Config file to validate (JSON)")

    ap.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Verbose logging output",
    )

    return ap.parse_args()


def run_full_optimization(args):
    """Run the full optimization pipeline."""
    print("\n" + "=" * 70)
    print("  FULL OPTIMIZATION PIPELINE")
    print("=" * 70)
    print(f"  Output directory: {args.output}")
    print(f"  Seeds: {args.seeds}")
    print(f"  Mode: {'fast' if args.mode_fast else 'deep'}")
    print("=" * 70 + "\n")

    mode = "fast" if args.mode_fast else "deep"
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    runner = ExperimentRunner(verbose=args.verbose)
    t0 = time.time()

    try:
        report = runner.run_full_optimization_pipeline(
            seeds=args.seeds, mode=mode, output_dir=str(output_dir)
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 70)
        print("  OPTIMIZATION COMPLETE")
        print("=" * 70)
        print(f"  Time elapsed: {elapsed / 60:.1f} minutes")
        print(f"  Best config saved to: {output_dir}/best_config.json")
        print(f"  Full report: {output_dir}/full_report.json")
        print("=" * 70 + "\n")

        # Print summary
        if "best_config" in report:
            print("  BEST CONFIGURATION:")
            print(json.dumps(report["best_config"], indent=2))

        if "final_metrics" in report:
            metrics = report["final_metrics"]
            print("\n  FINAL METRICS:")
            print(f"    recall@5:         {metrics.get('recall@5', 0):.4f}")
            print(f"    precision@5_mean: {metrics.get('precision@5_mean', 0):.4f}")
            print(f"    remediation_acc:  {metrics.get('remediation_acc', 0):.4f}")
            print(f"    weighted_score:   {metrics.get('weighted_score', 0):.4f}")

        return 0

    except Exception as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def run_weight_sweep(args):
    """Run a weight sweep experiment."""
    print("\n" + "=" * 70)
    print("  WEIGHT SWEEP EXPERIMENT")
    print("=" * 70)

    if not args.params:
        print("  ERROR: --param required for sweep mode")
        print("  Usage: --param NAME val1 val2 val3 ...", file=sys.stderr)
        return 1

    param_ranges = {}
    for param_spec in args.params:
        if len(param_spec) < 2:
            print(f"  ERROR: Invalid param spec: {param_spec}", file=sys.stderr)
            return 1
        name = param_spec[0]
        values = [float(v) if "." in v else int(v) for v in param_spec[1:]]
        param_ranges[name] = values
        print(f"  {name}: {values}")

    print(f"  Seeds: {args.seeds}")
    print(f"  Output: {args.output}")
    print("=" * 70 + "\n")

    mode = "fast" if args.mode_fast else "deep"
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    runner = ExperimentRunner(verbose=args.verbose)
    t0 = time.time()

    try:
        report = runner.run_weight_sweep_experiment(
            param_ranges=param_ranges,
            seeds=args.seeds,
            mode=mode,
            output_dir=str(output_dir),
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 70)
        print("  SWEEP COMPLETE")
        print("=" * 70)
        print(f"  Time elapsed: {elapsed / 60:.1f} minutes")
        print(f"  Configs tested: {report.get('total_configs', 0)}")
        print(f"  Best score: {report.get('best_score', 0):.4f}")
        print("=" * 70 + "\n")

        if "best_config" in report:
            print("  BEST CONFIG:")
            print(json.dumps(report["best_config"], indent=2))

        return 0

    except Exception as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def run_ablation_study(args):
    """Run an ablation study."""
    print("\n" + "=" * 70)
    print("  ABLATION STUDY")
    print("=" * 70)

    if not args.baseline or not args.variants:
        print("  ERROR: --baseline and --variants required for ablation mode")
        return 1

    print(f"  Baseline: {args.baseline}")
    print(f"  Variants: {args.variants}")
    print(f"  Seeds: {args.seeds}")
    print("=" * 70 + "\n")

    mode = "fast" if args.mode_fast else "deep"
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_mgr = ConfigManager()
    baseline = cfg_mgr.load_config(args.baseline)
    variants = [(Path(v).stem, cfg_mgr.load_config(v)) for v in args.variants]

    framework = AblationStudyFramework(verbose=args.verbose)
    t0 = time.time()

    try:
        report = framework.run_ablation(
            baseline_config=baseline,
            variant_configs=variants,
            seeds=args.seeds,
            mode=mode,
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 70)
        print("  ABLATION COMPLETE")
        print("=" * 70)
        print(f"  Time elapsed: {elapsed / 60:.1f} minutes")
        print("=" * 70 + "\n")

        # Print deltas
        if "deltas" in report:
            print("  METRIC DELTAS (vs Baseline):")
            for variant_name, deltas in report["deltas"].items():
                print(f"\n    {variant_name}:")
                for metric, delta in deltas.items():
                    sign = "+" if delta > 0 else ""
                    print(f"      {metric}: {sign}{delta:.4f}")

        # Save report
        report_file = output_dir / "ablation_report.json"
        with open(report_file, "w") as f:
            json.dump(report, f, indent=2)
        print(f"\n  Report saved to: {report_file}")

        return 0

    except Exception as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def run_targeted_optimization(args):
    """Run targeted optimization for a specific metric."""
    print("\n" + "=" * 70)
    print("  TARGETED OPTIMIZATION")
    print("=" * 70)
    print(f"  Metric: {args.metric}")
    print(f"  Initial config: {args.initial}")
    print(f"  Seeds: {args.seeds}")
    print("=" * 70 + "\n")

    if not args.metric or not args.initial:
        print("  ERROR: --metric and --initial required for targeted mode")
        return 1

    mode = "fast" if args.mode_fast else "deep"
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_mgr = ConfigManager()
    initial_config = cfg_mgr.load_config(args.initial)

    runner = ExperimentRunner(verbose=args.verbose)
    t0 = time.time()

    try:
        report = runner.run_targeted_optimization(
            focus_metric=args.metric,
            initial_config=initial_config,
            seeds=args.seeds,
            mode=mode,
            output_dir=str(output_dir),
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 70)
        print("  TARGETED OPTIMIZATION COMPLETE")
        print("=" * 70)
        print(f"  Time elapsed: {elapsed / 60:.1f} minutes")
        print(f"  Improvement: {report.get('improvement', 0):.4f}")
        print("=" * 70 + "\n")

        if "best_config" in report:
            print("  BEST CONFIG:")
            print(json.dumps(report["best_config"], indent=2))

        return 0

    except Exception as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def run_validation(args):
    """Validate a specific configuration."""
    print("\n" + "=" * 70)
    print("  VALIDATION RUN")
    print("=" * 70)
    print(f"  Config: {args.config}")
    print(f"  Seeds: {args.seeds}")
    print("=" * 70 + "\n")

    if not args.config:
        print("  ERROR: --config required for validate mode")
        return 1

    mode = "fast" if args.mode_fast else "deep"
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    cfg_mgr = ConfigManager()
    config = cfg_mgr.load_config(args.config)

    runner = OptimizedBenchmarkRunner(verbose=args.verbose)
    t0 = time.time()

    try:
        report = runner.run_optimized_benchmark(
            config=config,
            seeds=args.seeds,
            mode=mode,
            output_prefix=str(output_dir / "validation"),
        )
        elapsed = time.time() - t0

        print("\n" + "=" * 70)
        print("  VALIDATION COMPLETE")
        print("=" * 70)
        print(f"  Time elapsed: {elapsed / 60:.1f} minutes")
        print("=" * 70 + "\n")

        if "aggregated" in report:
            metrics = report["aggregated"]
            print("  FINAL METRICS:")
            print(f"    recall@5:         {metrics.get('recall@5', 0):.4f}")
            print(f"    precision@5_mean: {metrics.get('precision@5_mean', 0):.4f}")
            print(f"    remediation_acc:  {metrics.get('remediation_acc', 0):.4f}")
            print(
                f"    weighted_score:   {report.get('score', {}).get('weighted_score', 0):.4f}"
            )

        return 0

    except Exception as e:
        print(f"\n  ERROR: {e}", file=sys.stderr)
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


def main():
    """Main entry point."""
    args = parse_arguments()

    if args.mode == "full":
        return run_full_optimization(args)
    elif args.mode == "sweep":
        return run_weight_sweep(args)
    elif args.mode == "ablation":
        return run_ablation_study(args)
    elif args.mode == "targeted":
        return run_targeted_optimization(args)
    elif args.mode == "validate":
        return run_validation(args)
    else:
        print(f"Unknown mode: {args.mode}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
