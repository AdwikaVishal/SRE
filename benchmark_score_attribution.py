"""
benchmark_score_attribution.py — Score loss attribution analysis.

For every incident, determines exactly WHY score was lost:
- recall_miss: true family not in top 5
- precision_contamination: wrong family in top 5
- remediation_mismatch: right family but wrong action
- decoy_failure: should reject but didn't
- confidence_miscalibration: confidence misalignment
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional


class ScoreAttributionAnalyzer:
    """Analyzes score loss attribution at the incident level."""

    def __init__(self, diagnostic_json_file: str):
        """
        Load diagnostics from JSON file.

        Args:
            diagnostic_json_file: Path to JSON diagnostic export from BenchmarkDiagnosticsCollector
        """
        with open(diagnostic_json_file, "r") as f:
            data = json.load(f)
        self.incidents = data.get("incidents", [])
        self.metadata = data.get("metadata", {})

    def analyze_incident(self, diagnostic_record: dict[str, Any]) -> dict[str, Any]:
        """
        Determine WHY score was lost for a single incident.

        Args:
            diagnostic_record: Single incident from diagnostics list

        Returns:
            {
              incident_id,
              loss_category,
              loss_magnitude,
              details
            }
        """
        gt = diagnostic_record["ground_truth"]
        pred = diagnostic_record["prediction"]
        attr = diagnostic_record["scoring_attribution"]

        outcome = attr.get("match_outcome", "unknown")
        is_decoy = gt["is_decoy"]
        true_family = gt["family_id"]
        expected_remed = gt["expected_remediation"]

        loss_category = "no_loss"
        loss_magnitude = 0.0
        loss_details = {}

        # RECALL METRIC: was true family in top-5?
        if not is_decoy:
            top_5 = pred["top_5_families"]
            true_in_top5 = any(f["family"] == true_family for f in top_5)

            if not true_in_top5:
                # Recall miss: true family not in top-5
                loss_category = "recall_miss"
                loss_magnitude = 1.0  # Full penalty: -0.20 (30% of 0.20)

                # Attribution details
                pred_top1 = top_5[0]["family"] if top_5 else None
                loss_details = {
                    "true_family": true_family,
                    "predicted_rank1": pred_top1,
                    "top_5_families": [f["family"] for f in top_5],
                    "top_5_similarities": [round(f["similarity"], 3) for f in top_5],
                }

            elif outcome == "correct_rank1":
                # True family is in top-5, check remediation
                if expected_remed is not None:
                    top_remed = pred["top_remediation_action"]
                    if top_remed is None or top_remed["action"] != expected_remed:
                        loss_category = "remediation_mismatch"
                        loss_magnitude = 1.0  # Full penalty: -0.20 (20% of 1.0)
                        loss_details = {
                            "expected_action": expected_remed,
                            "suggested_action": (
                                top_remed["action"] if top_remed else None
                            ),
                            "suggested_confidence": (
                                top_remed["confidence"] if top_remed else 0.0
                            ),
                        }

        # PRECISION METRIC: wrong families in top-5
        if loss_category == "no_loss" and not is_decoy:
            top_5 = pred["top_5_families"]
            wrong_in_top5 = [f for f in top_5 if f["family"] != true_family]

            if wrong_in_top5 and outcome != "correct_rank1":
                # Precision contamination
                loss_category = "precision_contamination"
                loss_magnitude = 1.0  # Partial penalty

                loss_details = {
                    "true_family": true_family,
                    "contaminating_families": [
                        {
                            "family": f["family"],
                            "rank": f["rank"],
                            "similarity": round(f["similarity"], 3),
                        }
                        for f in wrong_in_top5
                    ],
                    "num_contaminating": len(wrong_in_top5),
                }

        # DECOY METRIC: should reject decoys (no confident match)
        if loss_category == "no_loss" and is_decoy:
            if outcome == "false_positive_decoy":
                loss_category = "decoy_false_positive"
                loss_magnitude = 1.0  # Full penalty

                top_5 = pred["top_5_families"]
                top_sim = top_5[0]["similarity"] if top_5 else 0.0

                loss_details = {
                    "is_decoy": True,
                    "predicted_family": top_5[0]["family"] if top_5 else None,
                    "predicted_similarity": round(top_sim, 3),
                    "confidence_threshold": 0.5,
                    "threshold_violation": top_sim >= 0.5,
                }

        return {
            "incident_id": diagnostic_record["meta"]["incident_id"],
            "seed": diagnostic_record["meta"]["seed"],
            "incident_idx": diagnostic_record["meta"]["incident_idx"],
            "loss_category": loss_category,
            "loss_magnitude": loss_magnitude,
            "details": loss_details,
        }

    def aggregate_loss_breakdown(self) -> dict[str, Any]:
        """
        Aggregate loss analysis across all incidents.

        Returns:
            {
              recall_miss: {count, total_loss},
              precision_contamination: {...},
              remediation_mismatch: {...},
              decoy_false_positive: {...},
              no_loss: {...},
              total_incidents,
              total_loss_points
            }
        """
        loss_counts = defaultdict(int)
        loss_totals = defaultdict(float)

        for diag in self.incidents:
            analysis = self.analyze_incident(diag)
            category = analysis["loss_category"]
            magnitude = analysis["loss_magnitude"]

            loss_counts[category] += 1
            loss_totals[category] += magnitude

        # Normalize by category to understand relative impact
        breakdown = {}
        for category in loss_counts.keys():
            count = loss_counts[category]
            total = loss_totals[category]
            breakdown[category] = {
                "count": count,
                "total_loss_points": round(total, 2),
                "avg_loss_per_incident": round(total / max(count, 1), 3),
            }

        total_loss = sum(loss_totals.values())

        return {
            "per_category": breakdown,
            "total_incidents": len(self.incidents),
            "total_loss_points": round(total_loss, 2),
            "avg_loss_per_incident": round(total_loss / max(len(self.incidents), 1), 3),
        }

    def highest_impact_failures(self, top_n: int = 20) -> list[dict[str, Any]]:
        """
        Rank incidents by impact: score points lost per incident.

        Returns:
            List of top N incidents with most lost points, sorted desc by loss
        """
        analyses = []
        for diag in self.incidents:
            analysis = self.analyze_incident(diag)
            if analysis["loss_magnitude"] > 0:
                analyses.append(analysis)

        # Sort by loss magnitude (descending) and by incident_id for determinism
        analyses.sort(key=lambda x: (-x["loss_magnitude"], x["incident_id"]))

        return analyses[:top_n]

    def loss_by_family(self) -> dict[Optional[int], dict[str, Any]]:
        """
        Aggregate loss by true family.

        Returns:
            Dict[family, {num_incidents, loss_count, categories}]
        """
        family_stats = defaultdict(
            lambda: {
                "num_incidents": 0,
                "loss_count": 0,
                "loss_categories": defaultdict(int),
            }
        )

        for diag in self.incidents:
            gt = diag["ground_truth"]
            family = gt["family_id"]

            analysis = self.analyze_incident(diag)
            stats = family_stats[family]

            stats["num_incidents"] += 1
            if analysis["loss_category"] != "no_loss":
                stats["loss_count"] += 1
                stats["loss_categories"][analysis["loss_category"]] += 1

        # Compute final stats
        result = {}
        for family, stats in family_stats.items():
            n = stats["num_incidents"]
            loss_cats = dict(stats["loss_categories"])
            result[family] = {
                "num_incidents": n,
                "loss_count": stats["loss_count"],
                "loss_rate": stats["loss_count"] / max(n, 1),
                "loss_categories": loss_cats,
            }

        return result

    def confidence_calibration_analysis(self) -> dict[str, Any]:
        """
        Analyze confidence miscalibration: actual vs predicted confidence.

        Returns:
            {
              high_confidence_wrong,
              low_confidence_right,
              mean_confidence_right,
              mean_confidence_wrong
            }
        """
        high_conf_wrong = 0  # confidence >= 0.5 but wrong
        low_conf_right = 0  # confidence < 0.5 but correct
        conf_right = []
        conf_wrong = []

        for diag in self.incidents:
            gt = diag["ground_truth"]

            if gt["is_decoy"]:
                continue

            pred = diag["prediction"]
            attr = diag["scoring_attribution"]

            outcome = attr.get("match_outcome", "unknown")
            is_correct = "correct" in outcome

            top_5 = pred["top_5_families"]
            if top_5:
                top_sim = top_5[0]["similarity"]

                if is_correct:
                    conf_right.append(top_sim)
                    if top_sim < 0.5:
                        low_conf_right += 1
                else:
                    conf_wrong.append(top_sim)
                    if top_sim >= 0.5:
                        high_conf_wrong += 1

        return {
            "high_confidence_but_wrong": high_conf_wrong,
            "low_confidence_but_correct": low_conf_right,
            "mean_confidence_when_correct": (
                sum(conf_right) / len(conf_right) if conf_right else 0.0
            ),
            "mean_confidence_when_wrong": (
                sum(conf_wrong) / len(conf_wrong) if conf_wrong else 0.0
            ),
            "confidence_separation": (
                (sum(conf_right) / len(conf_right))
                - (sum(conf_wrong) / len(conf_wrong))
                if (conf_right and conf_wrong)
                else 0.0
            ),
        }

    def export_json(self, filename: str) -> None:
        """Export complete score attribution analysis to JSON."""
        analysis = {
            "metadata": self.metadata,
            "loss_breakdown": self.aggregate_loss_breakdown(),
            "loss_by_family": self.loss_by_family(),
            "confidence_calibration": self.confidence_calibration_analysis(),
            "highest_impact_failures": self.highest_impact_failures(top_n=20),
        }

        with open(filename, "w") as f:
            json.dump(analysis, f, indent=2, default=str)
