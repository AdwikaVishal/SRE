"""
seed_wise_comparison.py — Seed-by-seed metric comparison and variance analysis.

Loads multiple benchmark runs and compares them seed-wise:
- Shows which seeds improve, which regress
- Analyzes metric variance across seeds
- Stability comparison (is variant more stable?)
- CSV export for detailed seed-level analysis

Usage:
    comparator = SeedWiseComparator()
    deltas = comparator.per_seed_deltas(
        "baseline_report.json",
        "variant_report.json"
    )
    variance = comparator.variance_analysis()
    report = comparator.stability_report()
"""

from __future__ import annotations

import csv
import json
from statistics import mean, stdev
from typing import Any, Optional


class SeedWiseComparator:
    """Compares runs seed-by-seed to track metric stability."""

    def __init__(self):
        """Initialize comparator."""
        self.baseline_data: Optional[dict] = None
        self.variant_data: Optional[dict] = None
        self.per_seed_results: list[dict] = []

    def load_runs(self, baseline_json: str, variant_json: str) -> None:
        """
        Load two benchmark runs for comparison.

        Args:
            baseline_json: Path to baseline report.json
            variant_json: Path to variant report.json
        """
        with open(baseline_json) as f:
            self.baseline_data = json.load(f)

        with open(variant_json) as f:
            self.variant_data = json.load(f)

    def per_seed_deltas(self) -> dict[int, dict[str, Any]]:
        """
        Compute per-seed metric deltas.

        Returns:
            {
                seed: {
                    "baseline_score": float,
                    "variant_score": float,
                    "delta": float,
                    "recall@5_delta": float,
                    "precision@5_mean_delta": float,
                    "remediation_acc_delta": float,
                    "latency_p95_ms_delta": float,
                    "direction": "improve" | "regress" | "same",
                }
            }
        """
        if not self.baseline_data or not self.variant_data:
            return {}

        baseline_by_seed = {
            s["seed"]: s for s in self.baseline_data.get("per_seed", [])
        }
        variant_by_seed = {s["seed"]: s for s in self.variant_data.get("per_seed", [])}

        results = {}

        for seed, baseline_run in baseline_by_seed.items():
            variant_run = variant_by_seed.get(seed)
            if not variant_run:
                continue

            baseline_summary = baseline_run.get("summary", {})
            variant_summary = variant_run.get("summary", {})

            baseline_score = float(baseline_summary.get("recall@5", 0.0)) * 0.30
            baseline_score += (
                float(baseline_summary.get("precision@5_mean", 0.0)) * 0.15
            )
            baseline_score += float(baseline_summary.get("remediation_acc", 0.0)) * 0.20
            # Latency scoring: ratio of budget to actual
            latency_ratio = min(
                1.0,
                2000.0 / max(float(baseline_summary.get("latency_p95_ms", 1.0)), 1e-6),
            )
            baseline_score += latency_ratio * 0.15

            variant_score = float(variant_summary.get("recall@5", 0.0)) * 0.30
            variant_score += float(variant_summary.get("precision@5_mean", 0.0)) * 0.15
            variant_score += float(variant_summary.get("remediation_acc", 0.0)) * 0.20
            latency_ratio = min(
                1.0,
                2000.0 / max(float(variant_summary.get("latency_p95_ms", 1.0)), 1e-6),
            )
            variant_score += latency_ratio * 0.15

            delta = variant_score - baseline_score
            direction = (
                "improve" if delta > 0.01 else ("regress" if delta < -0.01 else "same")
            )

            results[seed] = {
                "baseline_score": round(baseline_score, 4),
                "variant_score": round(variant_score, 4),
                "delta": round(delta, 4),
                "recall@5_delta": round(
                    float(variant_summary.get("recall@5", 0.0))
                    - float(baseline_summary.get("recall@5", 0.0)),
                    4,
                ),
                "precision@5_mean_delta": round(
                    float(variant_summary.get("precision@5_mean", 0.0))
                    - float(baseline_summary.get("precision@5_mean", 0.0)),
                    4,
                ),
                "remediation_acc_delta": round(
                    float(variant_summary.get("remediation_acc", 0.0))
                    - float(baseline_summary.get("remediation_acc", 0.0)),
                    4,
                ),
                "latency_p95_ms_delta": round(
                    float(variant_summary.get("latency_p95_ms", 0.0))
                    - float(baseline_summary.get("latency_p95_ms", 0.0)),
                    4,
                ),
                "direction": direction,
            }

        self.per_seed_results = list(results.values())
        return results

    def variance_analysis(self) -> dict[str, Any]:
        """
        Analyze metric variance across seeds.

        Returns:
            {
                "recall@5": {
                    "baseline_mean": float,
                    "baseline_std": float,
                    "variant_mean": float,
                    "variant_std": float,
                    "stability_improved": bool,
                },
                ...
            }
        """
        if not self.baseline_data or not self.variant_data:
            return {}

        baseline_by_seed = {
            s["seed"]: s for s in self.baseline_data.get("per_seed", [])
        }
        variant_by_seed = {s["seed"]: s for s in self.variant_data.get("per_seed", [])}

        metrics = ["recall@5", "precision@5_mean", "remediation_acc", "latency_p95_ms"]
        analysis = {}

        for metric in metrics:
            baseline_vals = [
                float(s.get("summary", {}).get(metric, 0.0))
                for s in baseline_by_seed.values()
            ]
            variant_vals = [
                float(s.get("summary", {}).get(metric, 0.0))
                for s in variant_by_seed.values()
            ]

            if not baseline_vals or not variant_vals:
                continue

            baseline_mean = mean(baseline_vals)
            baseline_std = stdev(baseline_vals) if len(baseline_vals) > 1 else 0.0
            variant_mean = mean(variant_vals)
            variant_std = stdev(variant_vals) if len(variant_vals) > 1 else 0.0

            # For latency, lower variance is better
            if metric == "latency_p95_ms":
                stability_improved = variant_std < baseline_std
            else:
                # For other metrics, higher mean is better
                # Stability: variance improved AND mean improved
                stability_improved = variant_std <= baseline_std

            analysis[metric] = {
                "baseline_mean": round(baseline_mean, 4),
                "baseline_std": round(baseline_std, 4),
                "variant_mean": round(variant_mean, 4),
                "variant_std": round(variant_std, 4),
                "stability_improved": stability_improved,
            }

        return analysis

    def stability_report(self) -> str:
        """
        Generate human-readable stability report.

        Returns:
            Markdown-formatted report
        """
        if not self.per_seed_results:
            self.per_seed_deltas()

        per_seed_dict = {
            r.get("seed", i): r for i, r in enumerate(self.per_seed_results)
        }

        variance = self.variance_analysis()

        lines = [
            "# Seed-wise Stability Report\n",
            "## Per-Seed Deltas\n\n",
        ]

        improve_count = sum(
            1 for r in self.per_seed_results if r["direction"] == "improve"
        )
        regress_count = sum(
            1 for r in self.per_seed_results if r["direction"] == "regress"
        )
        same_count = sum(1 for r in self.per_seed_results if r["direction"] == "same")

        lines.append(f"**Summary:**\n")
        lines.append(f"- Improved: {improve_count} seeds\n")
        lines.append(f"- Regressed: {regress_count} seeds\n")
        lines.append(f"- Same: {same_count} seeds\n\n")

        lines.append("**Per-Seed Scores:**\n\n")
        lines.append("| Seed | Baseline | Variant | Delta | Direction |\n")
        lines.append("|------|----------|---------|-------|----------|\n")

        for result in sorted(
            self.per_seed_results, key=lambda r: r.get("delta", 0), reverse=True
        ):
            seed = result.get("seed", "?")
            baseline = result.get("baseline_score", 0.0)
            variant = result.get("variant_score", 0.0)
            delta = result.get("delta", 0.0)
            direction = result.get("direction", "?")
            lines.append(
                f"| {seed} | {baseline:.4f} | {variant:.4f} | {delta:+.4f} | {direction} |\n"
            )

        lines.append("\n## Variance Analysis\n\n")

        for metric, stats in variance.items():
            lines.append(f"### {metric}\n")
            lines.append(
                f"- **Baseline:** μ={stats['baseline_mean']:.4f}, σ={stats['baseline_std']:.4f}\n"
            )
            lines.append(
                f"- **Variant:** μ={stats['variant_mean']:.4f}, σ={stats['variant_std']:.4f}\n"
            )
            stability = (
                "✓ More stable" if stats["stability_improved"] else "✗ Less stable"
            )
            lines.append(f"- **Stability:** {stability}\n\n")

        return "".join(lines)

    def export_csv(self, filename: str) -> None:
        """
        Export per-seed comparison to CSV.

        Args:
            filename: Output CSV file path
        """
        if not self.per_seed_results:
            self.per_seed_deltas()

        fieldnames = [
            "seed",
            "baseline_score",
            "variant_score",
            "delta",
            "recall@5_delta",
            "precision@5_mean_delta",
            "remediation_acc_delta",
            "latency_p95_ms_delta",
            "direction",
        ]

        with open(filename, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for result in sorted(
                self.per_seed_results, key=lambda r: r.get("delta", 0), reverse=True
            ):
                writer.writerow(result)


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    comparator = SeedWiseComparator()
    comparator.load_runs("baseline_report.json", "variant_report.json")

    # Get per-seed deltas
    deltas = comparator.per_seed_deltas()
    print("Per-seed deltas:")
    for seed, delta_info in deltas.items():
        print(f"  Seed {seed}: {delta_info['delta']:+.4f}")

    # Print stability report
    report = comparator.stability_report()
    print("\n" + report)

    # Export to CSV
    comparator.export_csv("seed_comparison.csv")
    print("\nExported to seed_comparison.csv")
