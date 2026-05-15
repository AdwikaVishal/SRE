"""
weight_sweep_framework.py — Deterministic hyperparameter sweep system.

Generates combinatorial or randomized parameter sweeps with stable ordering.
Evaluates each configuration against seed-based datasets.
Produces sweep results with scoring, importance analysis, and reproducibility.

Key features:
- Deterministic: identical runs produce identical results
- Combinatorial or random sampling
- Per-seed evaluation with timeout handling
- Weighted scoring (recall, precision, remediation, latency)
- Parameter importance attribution
"""

from __future__ import annotations

import itertools
import json
import random
import time
from dataclasses import asdict, dataclass
from statistics import mean, median
from typing import Any, Callable, Literal


@dataclass
class SweepResult:
    """Single configuration evaluation result."""

    config: dict[str, Any]
    weighted_score: float
    recall_at_5: float
    precision_at_5_mean: float
    remediation_acc: float
    latency_p95_ms: float
    per_seed_scores: list[float]  # Score for each seed
    eval_timestamp: str


class WeightSweepOptimizer:
    """
    Manages deterministic hyperparameter sweep and evaluation.

    Integrates with bench_run.py to evaluate engine configurations.
    Produces stable, reproducible results across runs.
    """

    def __init__(self):
        self.results: list[SweepResult] = []

    def generate_sweep_configs(
        self,
        parameter_ranges: dict[str, list[Any]],
        max_configs: int | None = None,
        seed: int = 42,
    ) -> list[dict[str, Any]]:
        """
        Generate parameter configurations from ranges.

        Produces combinatorial grid if <=5000 configs, else random sampling.
        Results are sorted deterministically for reproducibility.

        Args:
            parameter_ranges: {"param_name": [val1, val2, ...], ...}
            max_configs: Cap number of configs (forces random sampling if exceeded)
            seed: Random seed for sampling stability

        Returns:
            List of config dicts, sorted by param values (deterministic order)
        """
        param_names = sorted(parameter_ranges.keys())
        param_lists = [parameter_ranges[name] for name in param_names]

        # Count total combinations
        total_combos = 1
        for lst in param_lists:
            total_combos *= len(lst)

        # Decide: combinatorial or random sampling
        use_random = max_configs and total_combos > max_configs
        if use_random:
            use_random = total_combos > 5000

        if use_random and max_configs:
            # Random sampling
            rng = random.Random(seed)
            configs = []
            for _ in range(min(max_configs, total_combos)):
                config = {}
                for name, lst in zip(param_names, param_lists):
                    config[name] = rng.choice(lst)
                configs.append(config)
            # Deduplicate
            unique_configs = []
            seen = set()
            for cfg in configs:
                key = tuple(sorted(cfg.items()))
                if key not in seen:
                    unique_configs.append(cfg)
                    seen.add(key)
            configs = unique_configs
        else:
            # Full combinatorial grid
            combos = itertools.product(*param_lists)
            configs = [dict(zip(param_names, combo)) for combo in combos]

        # Sort deterministically
        configs.sort(key=lambda c: tuple(c.get(k) for k in param_names))

        return configs

    def evaluate_config(
        self,
        config: dict[str, Any],
        seed_list: list[int],
        bench_run_fn: Callable,
        mode: str = "fast",
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        """
        Evaluate a single configuration across multiple seeds.

        Runs the engine with the given config, measures:
        - recall@5
        - precision@5_mean
        - remediation_acc
        - latency_p95_ms

        Args:
            config: Parameter config dict to apply to engine
            seed_list: List of random seeds to evaluate on
            bench_run_fn: Callable that runs benchmark with config, returns report
            mode: "fast" or "deep" evaluation mode
            timeout_s: Timeout per seed in seconds

        Returns:
            {
                "weighted_score": float,
                "recall_at_5": float,
                "precision_at_5_mean": float,
                "remediation_acc": float,
                "latency_p95_ms": float,
                "per_seed_scores": list[float],
            }
        """
        per_seed_results = []

        for seed in seed_list:
            try:
                # Run benchmark with this config
                start_time = time.time()
                report = bench_run_fn(config, [seed], mode)
                elapsed = time.time() - start_time

                if elapsed > timeout_s:
                    continue

                agg = report.get("aggregated", {})
                per_seed_results.append(
                    {
                        "recall": agg.get("recall@5", 0.0),
                        "precision": agg.get("precision@5_mean", 0.0),
                        "remediation": agg.get("remediation_acc", 0.0),
                        "latency_p95": agg.get("latency_p95_ms", 0.0),
                    }
                )
            except Exception as e:
                # Timeout or error: penalize
                per_seed_results.append(
                    {
                        "recall": 0.0,
                        "precision": 0.0,
                        "remediation": 0.0,
                        "latency_p95": timeout_s * 1000,
                    }
                )

        if not per_seed_results:
            return {
                "weighted_score": 0.0,
                "recall_at_5": 0.0,
                "precision_at_5_mean": 0.0,
                "remediation_acc": 0.0,
                "latency_p95_ms": timeout_s * 1000,
                "per_seed_scores": [],
            }

        # Aggregate across seeds
        avg_recall = mean(r["recall"] for r in per_seed_results)
        avg_precision = mean(r["precision"] for r in per_seed_results)
        avg_remediation = mean(r["remediation"] for r in per_seed_results)
        p95_latency = max(r["latency_p95"] for r in per_seed_results)

        # Weighted score: emphasize recall > precision > remediation
        # Latency is soft penalty (normalized)
        latency_penalty = min(1.0, p95_latency / 2000.0)  # Asymptotes at 2000ms
        weighted = (
            0.50 * avg_recall
            + 0.30 * avg_precision
            + 0.15 * avg_remediation
            + 0.05 * (1.0 - latency_penalty)
        )

        per_seed_scores = [
            0.50 * r["recall"]
            + 0.30 * r["precision"]
            + 0.15 * r["remediation"]
            + 0.05 * (1.0 - min(1.0, r["latency_p95"] / 2000.0))
            for r in per_seed_results
        ]

        return {
            "weighted_score": round(weighted, 4),
            "recall_at_5": round(avg_recall, 4),
            "precision_at_5_mean": round(avg_precision, 4),
            "remediation_acc": round(avg_remediation, 4),
            "latency_p95_ms": round(p95_latency, 1),
            "per_seed_scores": [round(s, 4) for s in per_seed_scores],
        }

    def run_sweep(
        self,
        parameter_ranges: dict[str, list[Any]],
        seed_list: list[int],
        bench_run_fn: Callable,
        mode: str = "fast",
        max_configs: int | None = None,
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        """
        Execute full parameter sweep.

        Generates configs, evaluates each, ranks by weighted_score.

        Args:
            parameter_ranges: {"param": [vals], ...}
            seed_list: Seeds for evaluation
            bench_run_fn: Function to run benchmark
            mode: "fast" or "deep"
            max_configs: Max number of configs to evaluate
            timeout_s: Timeout per config

        Returns:
            {
                "best_config": dict,
                "best_score": float,
                "all_results": [SweepResult, ...],
                "parameter_importance": {param: impact_score, ...},
                "metadata": {
                    "total_configs": int,
                    "seeds_tested": list[int],
                    "mode": str,
                    "timestamp": str,
                }
            }
        """
        # Generate configs
        configs = self.generate_sweep_configs(parameter_ranges, max_configs=max_configs)

        print(f"[SWEEP] Evaluating {len(configs)} configurations...")
        self.results = []

        for i, config in enumerate(configs):
            print(f"  [{i + 1}/{len(configs)}] Evaluating config: {config}")

            score_dict = self.evaluate_config(
                config, seed_list, bench_run_fn, mode=mode, timeout_s=timeout_s
            )

            result = SweepResult(
                config=config,
                weighted_score=score_dict["weighted_score"],
                recall_at_5=score_dict["recall_at_5"],
                precision_at_5_mean=score_dict["precision_at_5_mean"],
                remediation_acc=score_dict["remediation_acc"],
                latency_p95_ms=score_dict["latency_p95_ms"],
                per_seed_scores=score_dict["per_seed_scores"],
                eval_timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            )
            self.results.append(result)

        # Sort by weighted_score descending
        self.results.sort(key=lambda r: r.weighted_score, reverse=True)

        # Compute parameter importance
        param_importance = self._compute_parameter_importance(configs)

        best = self.results[0] if self.results else None

        return {
            "best_config": best.config if best else {},
            "best_score": best.weighted_score if best else 0.0,
            "all_results": [
                {
                    "config": r.config,
                    "weighted_score": r.weighted_score,
                    "recall_at_5": r.recall_at_5,
                    "precision_at_5_mean": r.precision_at_5_mean,
                    "remediation_acc": r.remediation_acc,
                    "latency_p95_ms": r.latency_p95_ms,
                    "per_seed_scores": r.per_seed_scores,
                    "eval_timestamp": r.eval_timestamp,
                }
                for r in self.results
            ],
            "parameter_importance": param_importance,
            "metadata": {
                "total_configs": len(configs),
                "seeds_tested": seed_list,
                "mode": mode,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }

    def _compute_parameter_importance(
        self,
        all_configs: list[dict[str, Any]],
    ) -> dict[str, float]:
        """
        Estimate which parameters impact weighted_score most.

        For each parameter, measure score variance when that parameter varies.
        High variance = important parameter.

        Returns:
            {"param_name": importance_score (0.0-1.0), ...}
        """
        if not self.results or not all_configs:
            return {}

        # Group results by parameter value
        param_importance: dict[str, float] = {}

        all_params = set()
        for cfg in all_configs:
            all_params.update(cfg.keys())

        for param in all_params:
            # Group by param value
            groups: dict[Any, list[float]] = {}
            for result in self.results:
                val = result.config.get(param)
                if val is not None:
                    groups.setdefault(val, []).append(result.weighted_score)

            # Measure variance of group means
            if len(groups) > 1:
                group_means = [mean(scores) for scores in groups.values()]
                overall_mean = mean(group_means)
                variance = mean((m - overall_mean) ** 2 for m in group_means)
                param_importance[param] = round(variance, 4)

        # Normalize to [0, 1]
        max_importance = max(param_importance.values()) if param_importance else 1.0
        if max_importance > 0:
            for k in param_importance:
                param_importance[k] /= max_importance

        return param_importance

    def save_results(self, filepath: str) -> None:
        """Save sweep results to JSON file."""
        data = {
            "results": [
                {
                    "config": r.config,
                    "weighted_score": r.weighted_score,
                    "recall_at_5": r.recall_at_5,
                    "precision_at_5_mean": r.precision_at_5_mean,
                    "remediation_acc": r.remediation_acc,
                    "latency_p95_ms": r.latency_p95_ms,
                    "per_seed_scores": r.per_seed_scores,
                    "eval_timestamp": r.eval_timestamp,
                }
                for r in self.results
            ],
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)


# ============================================================================
# Helper: Integration with bench_run.py
# ============================================================================


def create_bench_runner(
    root_dir: str = "/Users/apple/SRE",
) -> Callable[[dict, list[int], str], dict]:
    """
    Factory to create a function that runs the benchmark with a given config.

    Args:
        root_dir: Path to SRE project root

    Returns:
        Callable[config, seed_list, mode] -> report dict
    """
    import os
    import sys

    sys.path.insert(0, root_dir)
    sys.path.insert(0, os.path.join(root_dir, "Anvil-P-E", "bench-p02-context"))

    from generator import GenConfig
    from harness import run

    def bench_runner(config: dict, seed_list: list[int], mode: str) -> dict:
        """Run benchmark with given config."""

        # Create adapter factory that injects config
        def adapter_factory():
            from adapters.engine import Engine

            engine = Engine()
            # Inject config into _cfg
            engine._cfg.update(config)
            return engine

        cfg = GenConfig(seed=seed_list[0], n_services=12, days=7)
        report = run(adapter_factory, cfg=cfg, mode=mode, seeds=seed_list, warmup=2)
        return report

    return bench_runner


# ============================================================================
# Example usage
# ============================================================================

if __name__ == "__main__":
    # Example: sweep over evidence_boost and same_cid_boost
    parameter_ranges = {
        "evidence_boost": [0.05, 0.10, 0.15, 0.20],
        "same_cid_boost": [0.20, 0.30, 0.40],
        "cross_cid_penalty": [0.10, 0.15, 0.20],
        "stageA_min_similarity": [0.50, 0.60, 0.70],
    }

    optimizer = WeightSweepOptimizer()

    # Generate configs
    configs = optimizer.generate_sweep_configs(parameter_ranges, max_configs=30)
    print(f"Generated {len(configs)} configurations")
    for i, cfg in enumerate(configs[:3]):
        print(f"  Config {i}: {cfg}")
