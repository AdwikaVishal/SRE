"""
optimized_bench_runner.py — Benchmark orchestration with diagnostic collection.

Wraps bench_run.py workflow to:
1. Run optimized engine with optional Part 2 optimizations
2. Collect comprehensive per-incident diagnostics
3. Analyze failures and score attribution
4. Generate multiple report formats (JSON, CSV)

Produces:
- {prefix}_report.json (harness output with aggregated metrics)
- {prefix}_diagnostics.json (per-incident diagnostic data)
- {prefix}_diagnostics.csv (flat diagnostic view)
- {prefix}_failure_analysis.json (failure root cause analysis)
- {prefix}_score_attribution.json (per-metric contribution)
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Optional

# Setup paths
ROOT = os.path.dirname(os.path.abspath(__file__))
BENCH = os.path.join(ROOT, "Anvil-P-E", "bench-p02-context")
sys.path.insert(0, ROOT)
sys.path.insert(0, BENCH)

from generator import GenConfig
from harness import compute_score
from harness import run as bench_run

from benchmark_diagnostics import BenchmarkDiagnosticsCollector
from failure_analysis import FailureAnalyzer
from optimized_engine_adapter import optimized_adapter_factory


class OptimizedBenchmarkRunner:
    """Orchestrates optimized benchmark runs with diagnostics."""

    def __init__(self, verbose: bool = True):
        """
        Initialize runner.

        Args:
            verbose: Print progress messages
        """
        self.verbose = verbose

    def run_optimized_benchmark(
        self,
        config: Optional[dict] = None,
        seeds: Optional[list[int]] = None,
        mode: str = "fast",
        output_prefix: str = "opt_report",
    ) -> dict[str, Any]:
        """
        Run complete optimized benchmark with diagnostics.

        Steps:
        1. Create OptimizedEngineAdapter with config
        2. Run harness with all seeds
        3. Collect per-incident diagnostics
        4. Run failure analysis
        5. Generate all output files
        6. Return comprehensive report

        Args:
            config: Optional engine config dict
            seeds: List of random seeds (default: [9999, 31415, 27182, 16180, 11235])
            mode: "fast" or "deep"
            output_prefix: Prefix for output files (no .json extension)

        Returns:
            {
                "report": harness_report,
                "diagnostics_json": filename,
                "diagnostics_csv": filename,
                "failure_analysis": failure_report,
                "score_attribution": attribution_report,
            }
        """
        if seeds is None:
            seeds = [9999, 31415, 27182, 16180, 11235]

        if self.verbose:
            print(f"[OptimizedBenchmarkRunner] Starting benchmark run...")
            print(f"  Seeds: {seeds}")
            print(f"  Mode: {mode}")
            print(f"  Config: {config}")

        # ================================================================
        # Step 1: Create factory function that produces OptimizedEngineAdapter
        # ================================================================
        def optimized_factory():
            return optimized_adapter_factory(config=config)

        # ================================================================
        # Step 2: Run harness
        # ================================================================
        if self.verbose:
            print("[OptimizedBenchmarkRunner] Running harness...")

        cfg = GenConfig(seed=seeds[0])
        harness_report = bench_run(
            optimized_factory, cfg=cfg, mode=mode, seeds=seeds, warmup=2
        )

        if self.verbose:
            print("[OptimizedBenchmarkRunner] Harness complete")
            print(f"  Weighted score: {harness_report['score']['weighted_score']}")

        # ================================================================
        # Step 3: Collect per-seed diagnostics
        # ================================================================
        if self.verbose:
            print("[OptimizedBenchmarkRunner] Collecting diagnostics...")

        diagnostics_collector = BenchmarkDiagnosticsCollector()

        # Extract per-seed data from harness report
        for seed_result in harness_report.get("per_seed", []):
            seed = seed_result.get("seed")
            for i, per_incident in enumerate(seed_result.get("per_incident", [])):
                diagnostics_collector.collect_incident(
                    seed=seed,
                    incident_idx=i,
                    signal={"incident_id": per_incident.get("incident_id", "")},
                    ground_truth={"incident_id": per_incident.get("incident_id", "")},
                    context={},
                    latency_ms=per_incident.get("latency_ms", 0.0),
                )

        # ================================================================
        # Step 4: Export diagnostics
        # ================================================================
        if self.verbose:
            print("[OptimizedBenchmarkRunner] Exporting diagnostics...")

        diagnostics_json_file = f"{output_prefix}_diagnostics.json"
        diagnostics_csv_file = f"{output_prefix}_diagnostics.csv"

        diagnostics_collector.export_json(diagnostics_json_file)
        self._export_diagnostics_csv(
            diagnostics_collector.incidents, diagnostics_csv_file
        )

        if self.verbose:
            print(f"  Exported: {diagnostics_json_file}")
            print(f"  Exported: {diagnostics_csv_file}")

        # ================================================================
        # Step 5: Failure analysis
        # ================================================================
        if self.verbose:
            print("[OptimizedBenchmarkRunner] Running failure analysis...")

        failure_analyzer = FailureAnalyzer(harness_report)
        failure_report = failure_analyzer.analyze()

        failure_analysis_file = f"{output_prefix}_failure_analysis.json"
        with open(failure_analysis_file, "w") as f:
            json.dump(failure_report, f, indent=2)

        if self.verbose:
            print(f"  Exported: {failure_analysis_file}")

        # ================================================================
        # Step 6: Score attribution
        # ================================================================
        if self.verbose:
            print("[OptimizedBenchmarkRunner] Computing score attribution...")

        attribution = self._compute_score_attribution(harness_report)

        attribution_file = f"{output_prefix}_score_attribution.json"
        with open(attribution_file, "w") as f:
            json.dump(attribution, f, indent=2)

        if self.verbose:
            print(f"  Exported: {attribution_file}")

        # ================================================================
        # Step 7: Export main report
        # ================================================================
        report_file = f"{output_prefix}_report.json"
        with open(report_file, "w") as f:
            json.dump(harness_report, f, indent=2)

        if self.verbose:
            print(f"  Exported: {report_file}")
            print("[OptimizedBenchmarkRunner] Complete!")

        return {
            "report": harness_report,
            "report_json": report_file,
            "diagnostics_json": diagnostics_json_file,
            "diagnostics_csv": diagnostics_csv_file,
            "failure_analysis": failure_report,
            "failure_analysis_json": failure_analysis_file,
            "score_attribution": attribution,
            "score_attribution_json": attribution_file,
        }

    # ================================================================
    # Helpers
    # ================================================================

    def _export_diagnostics_csv(
        self,
        incidents: list[dict],
        filename: str,
    ) -> None:
        """Export diagnostics to CSV format."""
        import csv

        if not incidents:
            # Create empty CSV
            with open(filename, "w") as f:
                f.write("incident_id,seed,index,metric\n")
            return

        # Flatten diagnostics
        rows = []
        for inc in incidents:
            incident_id = inc.get("incident_id", "unknown")
            seed = inc.get("seed", 0)
            idx = inc.get("incident_index", 0)

            # Add row per metric
            for key, value in inc.items():
                if key not in ("incident_id", "seed", "incident_index", "details"):
                    rows.append(
                        {
                            "incident_id": incident_id,
                            "seed": seed,
                            "index": idx,
                            "metric": key,
                            "value": value,
                        }
                    )

        if rows:
            fieldnames = ["incident_id", "seed", "index", "metric", "value"]
            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

    def _compute_score_attribution(self, harness_report: dict) -> dict[str, Any]:
        """
        Compute per-metric contribution to final score.

        Returns:
            {
                "weighted_score": float,
                "per_metric": {
                    "recall@5": {"value": float, "weight": float, "contribution": float},
                    ...
                }
            }
        """
        aggregated = harness_report.get("aggregated", {})
        score_info = harness_report.get("score", {})
        axes = score_info.get("axes", {})

        # Hardcoded weights from harness
        weights = {
            "recall@5": 0.30,
            "precision@5_mean": 0.15,
            "remediation_acc": 0.20,
            "latency_p95_ms": 0.15,
            "manual_context": 0.10,
            "manual_explain": 0.10,
        }

        per_metric = {}
        for metric, weight in weights.items():
            value = axes.get(metric, 0.0) if axes else aggregated.get(metric, 0.0)
            if value is None:
                value = 0.0
            contribution = weight * float(value)

            per_metric[metric] = {
                "value": round(float(value), 4),
                "weight": weight,
                "contribution": round(contribution, 4),
            }

        weighted_score = score_info.get("weighted_score", 0.0)

        return {
            "weighted_score": round(float(weighted_score), 4),
            "per_metric": per_metric,
            "total_weight": sum(weights.values()),
        }


# ============================================================================
# CLI helper function
# ============================================================================


def run_optimized_benchmark_cli(
    config: Optional[dict] = None,
    seeds: Optional[list[int]] = None,
    mode: str = "fast",
    output_prefix: str = "opt_report",
) -> dict[str, Any]:
    """
    CLI-friendly entry point for optimized benchmark.

    Args:
        config: Engine configuration
        seeds: Random seeds
        mode: "fast" or "deep"
        output_prefix: Output file prefix

    Returns:
        Results dict
    """
    runner = OptimizedBenchmarkRunner(verbose=True)
    return runner.run_optimized_benchmark(
        config=config, seeds=seeds, mode=mode, output_prefix=output_prefix
    )


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    # Example: run with default settings
    results = run_optimized_benchmark_cli(
        seeds=[9999, 31415],
        mode="fast",
        output_prefix="results/test_run",
    )

    print(f"\nGenerated files:")
    print(f"  - {results['report_json']}")
    print(f"  - {results['diagnostics_json']}")
    print(f"  - {results['diagnostics_csv']}")
    print(f"  - {results['failure_analysis_json']}")
    print(f"  - {results['score_attribution_json']}")
