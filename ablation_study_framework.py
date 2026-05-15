"""
ablation_study_framework.py — Ablation study runner for optimization experiments.

Compares baseline config vs multiple variant configs.
Runs each against same seeds and generates delta analysis.
Produces statistical significance and improvement magnitude reports.

Usage:
    framework = AblationStudyFramework()
    result = framework.run_ablation(
        baseline_config={...},
        variant_configs=[
            ("no_decoy", {use_decoy_suppression: False}),
            ("weak_stage_a", {stageA_min_similarity: 0.50}),
        ],
        seeds=[9999, 31415],
        mode="fast",
    )
"""

from __future__ import annotations

import json
from statistics import mean, stdev
from typing import Any, Optional

from optimized_bench_runner import OptimizedBenchmarkRunner


class AblationStudyFramework:
    """Runs ablation studies comparing configurations."""

    def __init__(self, verbose: bool = True):
        """
        Initialize ablation framework.

        Args:
            verbose: Print progress
        """
        self.verbose = verbose
        self.runner = OptimizedBenchmarkRunner(verbose=verbose)

    def run_ablation(
        self,
        baseline_config: dict[str, Any],
        variant_configs: list[tuple[str, dict[str, Any]]],
        seeds: Optional[list[int]] = None,
        mode: str = "fast",
    ) -> dict[str, Any]:
        """
        Run ablation study comparing baseline vs variants.

        Args:
            baseline_config: Baseline engine configuration
            variant_configs: List of (name, config) tuples
            seeds: Random seeds to evaluate on
            mode: "fast" or "deep"

        Returns:
            {
                "baseline": {
                    "config": dict,
                    "metrics": {recall@5, precision@5_mean, ...},
                    "score": float,
                },
                "variants": [
                    {
                        "name": str,
                        "config": dict,
                        "metrics": {...},
                        "score": float,
                        "deltas": {metric: delta, ...},
                        "improvements": {metric: bool, ...},
                        "magnitude": float,  # avg absolute delta
                    }
                ],
                "summary": {
                    "best_variant": str,
                    "best_delta": float,
                    "worst_variant": str,
                    "worst_delta": float,
                }
            }
        """
        if seeds is None:
            seeds = [9999, 31415, 27182]

        if self.verbose:
            print(f"[AblationStudyFramework] Starting ablation study")
            print(f"  Baseline config: {baseline_config}")
            print(f"  Variants: {len(variant_configs)}")
            print(f"  Seeds: {seeds}")

        # ================================================================
        # Step 1: Run baseline
        # ================================================================
        if self.verbose:
            print("[AblationStudyFramework] Running baseline...")

        baseline_result = self.runner.run_optimized_benchmark(
            config=baseline_config,
            seeds=seeds,
            mode=mode,
            output_prefix=".ablation_baseline",
        )

        baseline_metrics = self._extract_metrics(baseline_result["report"])
        baseline_score = baseline_result["report"]["score"]["weighted_score"]

        if self.verbose:
            print(f"  Baseline score: {baseline_score}")

        baseline_info = {
            "config": baseline_config,
            "metrics": baseline_metrics,
            "score": baseline_score,
        }

        # ================================================================
        # Step 2: Run variants
        # ================================================================
        variant_results = []

        for var_name, var_config in variant_configs:
            if self.verbose:
                print(f"[AblationStudyFramework] Running variant: {var_name}")

            var_result = self.runner.run_optimized_benchmark(
                config=var_config,
                seeds=seeds,
                mode=mode,
                output_prefix=f".ablation_variant_{var_name}",
            )

            var_metrics = self._extract_metrics(var_result["report"])
            var_score = var_result["report"]["score"]["weighted_score"]

            if self.verbose:
                print(f"  Variant {var_name} score: {var_score}")

            # Compute deltas
            deltas = self.compute_deltas(baseline_metrics, var_metrics)
            improvements = {k: (v > 0) for k, v in deltas.items()}
            magnitude = mean([abs(v) for v in deltas.values()])

            variant_info = {
                "name": var_name,
                "config": var_config,
                "metrics": var_metrics,
                "score": var_score,
                "deltas": deltas,
                "score_delta": var_score - baseline_score,
                "improvements": improvements,
                "magnitude": round(magnitude, 4),
            }

            variant_results.append(variant_info)

        # ================================================================
        # Step 3: Summarize
        # ================================================================
        best_var = max(variant_results, key=lambda v: v["score"])
        worst_var = min(variant_results, key=lambda v: v["score"])

        summary = {
            "best_variant": best_var["name"],
            "best_delta": round(best_var["score_delta"], 4),
            "worst_variant": worst_var["name"],
            "worst_delta": round(worst_var["score_delta"], 4),
            "num_variants": len(variant_results),
        }

        if self.verbose:
            print(
                f"[AblationStudyFramework] Best variant: {summary['best_variant']} "
                f"({summary['best_delta']:+.4f})"
            )

        return {
            "baseline": baseline_info,
            "variants": variant_results,
            "summary": summary,
        }

    def compute_deltas(
        self,
        baseline_metrics: dict[str, float],
        variant_metrics: dict[str, float],
    ) -> dict[str, float]:
        """
        Compute metric deltas (variant - baseline).

        Args:
            baseline_metrics: Baseline metric values
            variant_metrics: Variant metric values

        Returns:
            {metric: delta_value, ...}
        """
        deltas = {}

        for metric in baseline_metrics.keys():
            baseline_val = baseline_metrics.get(metric, 0.0)
            variant_val = variant_metrics.get(metric, 0.0)
            delta = variant_val - baseline_val
            deltas[metric] = round(delta, 4)

        return deltas

    def report_ablation(self, ablation_result: dict[str, Any]) -> str:
        """
        Generate human-readable markdown report from ablation results.

        Args:
            ablation_result: Result from run_ablation()

        Returns:
            Markdown-formatted report string
        """
        baseline = ablation_result["baseline"]
        variants = ablation_result["variants"]
        summary = ablation_result["summary"]

        lines = [
            "# Ablation Study Report\n",
            f"## Baseline Configuration\n",
            f"**Score:** {baseline['score']:.4f}\n",
            f"**Config:**\n```\n{json.dumps(baseline['config'], indent=2)}\n```\n",
            f"**Metrics:**\n",
        ]

        for metric, value in baseline["metrics"].items():
            lines.append(f"- {metric}: {value:.4f}\n")

        lines.append(f"\n## Variants\n")

        for variant in variants:
            lines.append(f"\n### {variant['name']}\n")
            lines.append(
                f"**Score:** {variant['score']:.4f} ({variant['score_delta']:+.4f})\n"
            )
            lines.append(
                f"**Config Changes:**\n```\n"
                f"{json.dumps(variant['config'], indent=2)}\n```\n"
            )
            lines.append(f"**Metric Deltas (variant - baseline):**\n")

            for metric, delta in variant["deltas"].items():
                direction = "↑" if delta > 0 else ("↓" if delta < 0 else "→")
                lines.append(f"- {metric}: {delta:+.4f} {direction}\n")

            lines.append(f"\n**Improvement Magnitude:** {variant['magnitude']:.4f}\n")

        lines.append(f"\n## Summary\n")
        lines.append(
            f"- **Best variant:** {summary['best_variant']} "
            f"({summary['best_delta']:+.4f})\n"
        )
        lines.append(
            f"- **Worst variant:** {summary['worst_variant']} "
            f"({summary['worst_delta']:+.4f})\n"
        )
        lines.append(f"- **Total variants tested:** {summary['num_variants']}\n")

        return "".join(lines)

    # ================================================================
    # Helpers
    # ================================================================

    def _extract_metrics(self, harness_report: dict) -> dict[str, float]:
        """Extract key metrics from harness report."""
        aggregated = harness_report.get("aggregated", {})
        score_info = harness_report.get("score", {})
        axes = score_info.get("axes", {})

        return {
            "recall@5": float(axes.get("recall@5", 0.0)),
            "precision@5_mean": float(axes.get("precision@5_mean", 0.0)),
            "remediation_acc": float(axes.get("remediation_acc", 0.0)),
            "latency_p95_ms": float(aggregated.get("latency_p95_ms", 0.0)),
        }


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    baseline = {
        "stageA_min_similarity": 0.60,
        "decoy_similarity_cap": 0.45,
    }

    variants = [
        ("stronger_stage_a", {"stageA_min_similarity": 0.65}),
        ("weaker_decoy_cap", {"decoy_similarity_cap": 0.40}),
    ]

    framework = AblationStudyFramework(verbose=True)
    result = framework.run_ablation(
        baseline_config=baseline,
        variant_configs=variants,
        seeds=[9999, 31415],
        mode="fast",
    )

    # Print report
    report = framework.report_ablation(result)
    print(report)

    # Save results
    with open("ablation_results.json", "w") as f:
        json.dump(result, f, indent=2)
