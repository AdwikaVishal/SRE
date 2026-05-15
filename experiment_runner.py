"""
experiment_runner.py — Orchestrates end-to-end optimization experiments.

High-level automation for:
1. Weight sweep experiments: grid search over parameters
2. Targeted optimization: focus on specific weak metrics
3. Full pipeline: complete optimization workflow

Integrates ablation studies, seed-wise comparison, and failure analysis.
Produces comprehensive reports with all intermediate results.

Usage:
    runner = ExperimentRunner()

    # Quick weight sweep
    result = runner.run_weight_sweep_experiment(
        param_ranges={"stageA_min_similarity": [0.50, 0.55, 0.60, 0.65]},
        seeds=[9999, 31415],
        mode="fast",
        output_dir="results/sweep1/",
    )

    # Targeted optimization on weak metric
    result = runner.run_targeted_optimization(
        focus_metric="precision@5_mean",
        initial_config={...},
        seeds=[9999, 31415],
        output_dir="results/precision_opt/",
    )

    # Full pipeline
    result = runner.run_full_optimization_pipeline(
        seeds=[9999, 31415, 27182],
        mode="fast",
        output_dir="results/full_opt/",
    )
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from ablation_study_framework import AblationStudyFramework
from optimized_bench_runner import OptimizedBenchmarkRunner
from seed_wise_comparison import SeedWiseComparator
from weight_sweep_framework import WeightSweepOptimizer


class ExperimentRunner:
    """Orchestrates end-to-end optimization experiments."""

    def __init__(self, verbose: bool = True):
        """
        Initialize experiment runner.

        Args:
            verbose: Print progress messages
        """
        self.verbose = verbose
        self.bench_runner = OptimizedBenchmarkRunner(verbose=verbose)
        self.ablation_framework = AblationStudyFramework(verbose=verbose)

    def run_weight_sweep_experiment(
        self,
        param_ranges: dict[str, list[Any]],
        seeds: Optional[list[int]] = None,
        mode: str = "fast",
        output_dir: str = "results/sweep/",
    ) -> dict[str, Any]:
        """
        Run weight sweep optimization experiment.

        Steps:
        1. Generate parameter grid
        2. Evaluate each config on seeds
        3. Run ablation on top-5 configs
        4. Produce detailed report

        Args:
            param_ranges: Parameter ranges for sweep
                {"stageA_min_similarity": [0.50, 0.55, 0.60, 0.65]}
            seeds: Random seeds
            mode: "fast" or "deep"
            output_dir: Output directory for results

        Returns:
            {
                "sweep_results": list of config results,
                "top_5": [configs with highest scores],
                "best_config": best_config dict,
                "ablation_report": ablation comparison,
            }
        """
        if seeds is None:
            seeds = [9999, 31415, 27182]

        os.makedirs(output_dir, exist_ok=True)

        if self.verbose:
            print(f"[ExperimentRunner] Starting weight sweep experiment")
            print(f"  Parameter ranges: {param_ranges}")
            print(f"  Output dir: {output_dir}")

        # ================================================================
        # Step 1: Generate sweep configs
        # ================================================================
        optimizer = WeightSweepOptimizer()
        sweep_configs = optimizer.generate_sweep_configs(
            param_ranges, max_configs=50, seed=42
        )

        if self.verbose:
            print(f"[ExperimentRunner] Generated {len(sweep_configs)} sweep configs")

        # ================================================================
        # Step 2: Evaluate configs
        # ================================================================
        sweep_results = []

        for i, config in enumerate(sweep_configs):
            if self.verbose:
                print(
                    f"[ExperimentRunner] Evaluating config {i + 1}/{len(sweep_configs)}"
                )

            result = self.bench_runner.run_optimized_benchmark(
                config=config,
                seeds=seeds,
                mode=mode,
                output_prefix=f"{output_dir}.sweep_config_{i}",
            )

            score = result["report"]["score"]["weighted_score"]
            sweep_results.append(
                {
                    "config_idx": i,
                    "config": config,
                    "score": score,
                    "result_file": result["report_json"],
                }
            )

        # Sort by score
        sweep_results.sort(key=lambda x: x["score"], reverse=True)

        if self.verbose:
            print(f"[ExperimentRunner] Best score: {sweep_results[0]['score']:.4f}")

        # ================================================================
        # Step 3: Run ablation on top-5
        # ================================================================
        top_5 = sweep_results[:5]
        best_config = top_5[0]["config"]

        if self.verbose:
            print(f"[ExperimentRunner] Running ablation on top-5 configs")

        variant_configs = [
            (f"variant_{i}", cfg["config"]) for i, cfg in enumerate(top_5[1:], 1)
        ]

        ablation_result = self.ablation_framework.run_ablation(
            baseline_config=best_config,
            variant_configs=variant_configs,
            seeds=seeds,
            mode=mode,
        )

        # ================================================================
        # Step 4: Save results
        # ================================================================
        sweep_results_file = os.path.join(output_dir, "sweep_results.json")
        with open(sweep_results_file, "w") as f:
            json.dump(sweep_results, f, indent=2)

        top_5_file = os.path.join(output_dir, "top_5_configs.json")
        with open(top_5_file, "w") as f:
            json.dump([cfg["config"] for cfg in top_5], f, indent=2)

        best_config_file = os.path.join(output_dir, "best_config.json")
        with open(best_config_file, "w") as f:
            json.dump(best_config, f, indent=2)

        ablation_file = os.path.join(output_dir, "ablation_report.json")
        with open(ablation_file, "w") as f:
            json.dump(ablation_result, f, indent=2)

        if self.verbose:
            print(f"[ExperimentRunner] Saved sweep results to {output_dir}")

        return {
            "sweep_results": sweep_results,
            "top_5": top_5,
            "best_config": best_config,
            "best_score": sweep_results[0]["score"],
            "ablation_report": ablation_result,
            "output_dir": output_dir,
        }

    def run_targeted_optimization(
        self,
        focus_metric: str,
        initial_config: Optional[dict] = None,
        seeds: Optional[list[int]] = None,
        mode: str = "fast",
        output_dir: str = "results/targeted/",
    ) -> dict[str, Any]:
        """
        Run targeted optimization focusing on one weak metric.

        Suggests parameter adjustments to improve the target metric.
        Uses heuristic rules and manual guidance.

        Args:
            focus_metric: Metric to optimize ("recall@5", "precision@5_mean", etc.)
            initial_config: Starting configuration
            seeds: Random seeds
            mode: "fast" or "deep"
            output_dir: Output directory

        Returns:
            {
                "focus_metric": str,
                "recommendations": list of suggested adjustments,
                "best_config": optimized config,
                "best_score": final score,
            }
        """
        if seeds is None:
            seeds = [9999, 31415, 27182]

        if initial_config is None:
            initial_config = {}

        os.makedirs(output_dir, exist_ok=True)

        if self.verbose:
            print(f"[ExperimentRunner] Starting targeted optimization")
            print(f"  Focus metric: {focus_metric}")

        # ================================================================
        # Generate optimization suggestions based on metric
        # ================================================================
        recommendations = self._generate_metric_recommendations(focus_metric)

        if self.verbose:
            print(
                f"[ExperimentRunner] Generated {len(recommendations)} recommendations"
            )

        # ================================================================
        # Test recommendations
        # ================================================================
        tested_configs = []

        for i, (name, config_delta) in enumerate(recommendations):
            config = {**initial_config, **config_delta}

            if self.verbose:
                print(f"[ExperimentRunner] Testing recommendation {i + 1}: {name}")

            result = self.bench_runner.run_optimized_benchmark(
                config=config,
                seeds=seeds,
                mode=mode,
                output_prefix=f"{output_dir}.rec_{i}",
            )

            score = result["report"]["score"]["weighted_score"]
            metric_val = self._extract_metric_value(result["report"], focus_metric)

            tested_configs.append(
                {
                    "recommendation": name,
                    "config": config,
                    "score": score,
                    "target_metric_value": metric_val,
                }
            )

        # Sort by target metric
        tested_configs.sort(key=lambda x: x["target_metric_value"], reverse=True)

        best_config = tested_configs[0]["config"]
        best_score = tested_configs[0]["score"]

        # ================================================================
        # Save results
        # ================================================================
        results_file = os.path.join(output_dir, f"{focus_metric}_optimization.json")
        with open(results_file, "w") as f:
            json.dump(
                {
                    "focus_metric": focus_metric,
                    "tested_configs": tested_configs,
                    "best_config": best_config,
                    "best_score": best_score,
                },
                f,
                indent=2,
            )

        if self.verbose:
            print(f"[ExperimentRunner] Targeted optimization complete")
            print(f"  Best score: {best_score:.4f}")

        return {
            "focus_metric": focus_metric,
            "recommendations": recommendations,
            "tested_configs": tested_configs,
            "best_config": best_config,
            "best_score": best_score,
            "output_dir": output_dir,
        }

    def run_full_optimization_pipeline(
        self,
        seeds: Optional[list[int]] = None,
        mode: str = "fast",
        output_dir: str = "results/full_opt/",
    ) -> dict[str, Any]:
        """
        Run complete optimization pipeline.

        Steps:
        1. Baseline diagnostic run
        2. Weight sweep for broad parameter search
        3. Failure analysis on baseline
        4. Targeted optimization on weak metrics
        5. Final validation run with best config

        Args:
            seeds: Random seeds
            mode: "fast" or "deep"
            output_dir: Output directory

        Returns:
            Comprehensive optimization report
        """
        if seeds is None:
            seeds = [9999, 31415, 27182]

        os.makedirs(output_dir, exist_ok=True)

        if self.verbose:
            print(f"[ExperimentRunner] Starting full optimization pipeline")

        # ================================================================
        # Step 1: Baseline run
        # ================================================================
        if self.verbose:
            print("[ExperimentRunner] Step 1: Baseline diagnostic run")

        baseline_result = self.bench_runner.run_optimized_benchmark(
            config=None,  # Use defaults
            seeds=seeds,
            mode=mode,
            output_prefix=os.path.join(output_dir, "baseline"),
        )

        baseline_score = baseline_result["report"]["score"]["weighted_score"]

        # ================================================================
        # Step 2: Weight sweep
        # ================================================================
        if self.verbose:
            print("[ExperimentRunner] Step 2: Weight sweep optimization")

        param_ranges = {
            "stageA_min_similarity": [0.50, 0.55, 0.60, 0.65, 0.70],
            "decoy_similarity_cap": [0.35, 0.40, 0.45, 0.50],
        }

        sweep_result = self.run_weight_sweep_experiment(
            param_ranges=param_ranges,
            seeds=seeds,
            mode=mode,
            output_dir=os.path.join(output_dir, "sweep"),
        )

        best_sweep_config = sweep_result["best_config"]
        best_sweep_score = sweep_result["best_score"]

        # ================================================================
        # Step 3: Final validation
        # ================================================================
        if self.verbose:
            print("[ExperimentRunner] Step 3: Final validation run")

        final_result = self.bench_runner.run_optimized_benchmark(
            config=best_sweep_config,
            seeds=seeds,
            mode=mode,
            output_prefix=os.path.join(output_dir, "final_validated"),
        )

        final_score = final_result["report"]["score"]["weighted_score"]

        # ================================================================
        # Compile final report
        # ================================================================
        final_report = {
            "baseline": {
                "score": baseline_score,
                "report_file": baseline_result["report_json"],
            },
            "sweep": sweep_result,
            "final": {
                "score": final_score,
                "config": best_sweep_config,
                "report_file": final_result["report_json"],
            },
            "improvement": {
                "score_delta": final_score - baseline_score,
                "score_improvement_pct": round(
                    ((final_score - baseline_score) / baseline_score * 100), 2
                ),
            },
        }

        # Save final report
        final_report_file = os.path.join(output_dir, "final_optimization_report.json")
        with open(final_report_file, "w") as f:
            json.dump(final_report, f, indent=2)

        if self.verbose:
            print(f"[ExperimentRunner] Pipeline complete!")
            print(f"  Baseline score: {baseline_score:.4f}")
            print(f"  Final score: {final_score:.4f}")
            print(
                f"  Improvement: {final_report['improvement']['score_improvement_pct']:+.2f}%"
            )

        return final_report

    # ================================================================
    # Helpers
    # ================================================================

    def _generate_metric_recommendations(
        self, focus_metric: str
    ) -> list[tuple[str, dict]]:
        """
        Generate tuning recommendations for a metric.

        Args:
            focus_metric: Target metric

        Returns:
            List of (name, config_delta) tuples
        """
        if focus_metric == "precision@5_mean":
            return [
                ("Stronger Stage A threshold", {"stageA_min_similarity": 0.70}),
                ("Lower decoy cap", {"decoy_similarity_cap": 0.35}),
                (
                    "Combined strict",
                    {
                        "stageA_min_similarity": 0.70,
                        "decoy_similarity_cap": 0.35,
                    },
                ),
            ]
        elif focus_metric == "recall@5":
            return [
                ("Weaker Stage A threshold", {"stageA_min_similarity": 0.50}),
                ("Higher decoy cap", {"decoy_similarity_cap": 0.55}),
                (
                    "Combined permissive",
                    {
                        "stageA_min_similarity": 0.50,
                        "decoy_similarity_cap": 0.55,
                    },
                ),
            ]
        elif focus_metric == "remediation_acc":
            return [
                ("Increase min history", {"min_history_count": 3}),
                ("Higher success prior", {"prior_success_rate": 0.60}),
                (
                    "Combined",
                    {
                        "min_history_count": 3,
                        "prior_success_rate": 0.60,
                    },
                ),
            ]
        elif focus_metric == "latency_p95_ms":
            return [
                ("Reduce candidates", {"max_results": 3}),
                ("Faster evidence check", {"stageB_min_similarity": 0.60}),
            ]
        else:
            return [
                ("Balanced", {}),
                ("Aggressive precision", {"stageA_min_similarity": 0.65}),
            ]

    def _extract_metric_value(self, harness_report: dict, metric: str) -> float:
        """Extract metric value from harness report."""
        score_info = harness_report.get("score", {})
        axes = score_info.get("axes", {})
        return float(axes.get(metric, 0.0))


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    runner = ExperimentRunner(verbose=True)

    # Quick weight sweep
    print("=== Weight Sweep ===\n")
    sweep_result = runner.run_weight_sweep_experiment(
        param_ranges={
            "stageA_min_similarity": [0.55, 0.60, 0.65],
        },
        seeds=[9999, 31415],
        mode="fast",
        output_dir="results/sweep_demo/",
    )
    print(f"Best config from sweep: {sweep_result['best_config']}\n")

    # Targeted optimization
    print("=== Targeted Optimization ===\n")
    targeted_result = runner.run_targeted_optimization(
        focus_metric="precision@5_mean",
        initial_config=sweep_result["best_config"],
        seeds=[9999, 31415],
        mode="fast",
        output_dir="results/precision_opt_demo/",
    )
    print(f"Best config from targeting: {targeted_result['best_config']}\n")
