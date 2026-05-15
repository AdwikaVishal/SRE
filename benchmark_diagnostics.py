"""
benchmark_diagnostics.py — Benchmark harness extension for diagnostic collection.

Integrates with the bench_run.py workflow to collect per-incident diagnostics.
Produces JSON and CSV export suitable for analysis.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from typing import Any

from diagnostic_extractor import DiagnosticExtractor


class BenchmarkDiagnosticsCollector:
    """Collects per-incident diagnostics during benchmark evaluation."""

    def __init__(self):
        """Initialize the diagnostics collector."""
        self.incidents: list[dict[str, Any]] = []
        self.seed_map: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def collect_incident(
        self,
        seed: int,
        incident_idx: int,
        signal: dict[str, Any],
        ground_truth: dict[str, Any],
        context: dict[str, Any],
        latency_ms: float,
    ) -> None:
        """
        Collect diagnostic data for a single incident.

        Args:
            seed: Random seed for dataset generation
            incident_idx: Index in the eval signal list
            signal: IncidentSignal dict (incident_id, ts, trigger, service)
            ground_truth: GT dict with family, expected_remediation
            context: Context output from engine (related_events, similar_past_incidents, etc.)
            latency_ms: Query latency in milliseconds
        """
        # Ensure ground_truth has incident_id from signal
        if "incident_id" not in ground_truth and "incident_id" in signal:
            ground_truth = {**ground_truth, "incident_id": signal["incident_id"]}

        # Extract diagnostics
        diag = DiagnosticExtractor.dump_incident_diagnostics(
            seed=seed,
            incident_idx=incident_idx,
            ground_truth=ground_truth,
            prediction=context,
            related_events=context.get("related_events", []),
            similar_matches=context.get("similar_past_incidents", []),
            remediations=context.get("suggested_remediations", []),
            latency_ms=latency_ms,
        )

        self.incidents.append(diag)
        self.seed_map[seed].append(diag)

    def export_json(self, filename: str) -> None:
        """Export all diagnostics to JSON."""
        with open(filename, "w") as f:
            json.dump(
                {
                    "metadata": {
                        "total_incidents": len(self.incidents),
                        "num_seeds": len(self.seed_map),
                        "seeds": sorted(self.seed_map.keys()),
                    },
                    "incidents": self.incidents,
                },
                f,
                indent=2,
                default=str,
            )

    def export_csv(self, filename: str) -> None:
        """Export diagnostics summary to CSV (one row per incident)."""
        if not self.incidents:
            return

        # Flatten diagnostic records into CSV rows
        rows = []
        for diag in self.incidents:
            meta = diag["meta"]
            gt = diag["ground_truth"]
            pred = diag["prediction"]
            attr = diag["scoring_attribution"]
            evid = diag["graph_evidence"]

            # Top-5 families (flattened)
            top_5 = pred["top_5_families"]
            top_families = (
                ";".join(f"{f['rank']}:{f['family']}" for f in top_5)
                if top_5
                else "none"
            )
            top_sims = (
                ";".join(f"{f['rank']}:{f['similarity']:.3f}" for f in top_5)
                if top_5
                else "none"
            )

            row = {
                "seed": meta["seed"],
                "incident_idx": meta["incident_idx"],
                "incident_id": meta["incident_id"],
                "latency_ms": meta["query_latency_ms"],
                "true_family": gt["family_id"],
                "is_decoy": gt["is_decoy"],
                "canonical_service": gt["canonical_service_id"],
                "expected_remediation": gt["expected_remediation"],
                "pred_top_5_families": top_families,
                "pred_top_5_similarities": top_sims,
                "pred_num_confident_matches": pred["num_confident_matches"],
                "top_remediation_action": (
                    pred["top_remediation_action"]["action"]
                    if pred["top_remediation_action"]
                    else "none"
                ),
                "top_remediation_confidence": (
                    pred["top_remediation_action"]["confidence"]
                    if pred["top_remediation_action"]
                    else 0.0
                ),
                "match_outcome": attr.get("match_outcome", "unknown"),
                "has_deploy": evid["has_deploy"],
                "has_metric": evid["has_metric"],
                "has_trace_or_log": evid["has_trace_or_log"],
                "has_remediation": evid["has_remediation"],
                "total_events": evid["total_events"],
            }
            rows.append(row)

        # Write CSV
        if rows:
            fieldnames = list(rows[0].keys())
            with open(filename, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

    def compute_failure_stats(self) -> dict[str, Any]:
        """Compute failure mode breakdown across all collected incidents."""
        stats = {
            "total_incidents": len(self.incidents),
            "recall_miss": 0,
            "precision_contamination": 0,
            "remediation_mismatch": 0,
            "decoy_false_positive": 0,
            "correct": 0,
        }

        remediation_failures = 0
        decoy_failures = 0

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]
            attr = diag["scoring_attribution"]

            outcome = attr.get("match_outcome", "unknown")
            is_decoy = gt["is_decoy"]
            expected_remed = gt["expected_remediation"]

            # Categorize match outcome
            if outcome == "correct_rank1":
                # Check remediation match
                top_remed = pred["top_remediation_action"]
                if expected_remed is not None:
                    if top_remed and top_remed["action"] == expected_remed:
                        stats["correct"] += 1
                    else:
                        stats["remediation_mismatch"] += 1
                        remediation_failures += 1
                else:
                    stats["correct"] += 1

            elif outcome == "correct_decoy_rejection":
                stats["correct"] += 1

            elif outcome == "wrong_rank1_recall_miss":
                stats["recall_miss"] += 1

            elif outcome == "false_positive_decoy":
                stats["decoy_false_positive"] += 1
                decoy_failures += 1

        return {
            **stats,
            "failure_summary": {
                "total_failures": (
                    stats["recall_miss"]
                    + stats["precision_contamination"]
                    + stats["remediation_mismatch"]
                    + stats["decoy_false_positive"]
                ),
                "remediation_failures": remediation_failures,
                "decoy_failures": decoy_failures,
                "success_rate": (stats["correct"] / max(stats["total_incidents"], 1)),
            },
        }
