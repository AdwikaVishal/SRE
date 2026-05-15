"""
failure_analysis.py — Comprehensive failure mode analysis.

Loads diagnostic JSON and computes detailed failure breakdowns:
- Confusion matrices
- Contamination analysis
- Family-level precision
- Remediation mismatches
- Decoy failures
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Optional


class FailureAnalyzer:
    """Analyzes failure modes from diagnostic data."""

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

    def compute_confusion_matrix(self) -> dict[Optional[int], dict[Optional[int], int]]:
        """
        Build predicted vs true family confusion matrix.

        Returns:
            Dict[predicted_family, Dict[true_family, count]]
        """
        confusion = defaultdict(lambda: defaultdict(int))

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            true_family = gt["family_id"]
            top_5 = pred["top_5_families"]

            if top_5:
                pred_family = top_5[0]["family"]
                confusion[pred_family][true_family] += 1

        # Convert to nested dict
        return {pred: dict(true_counts) for pred, true_counts in confusion.items()}

    def compute_contamination_matrix(
        self,
    ) -> dict[Optional[int], dict[Optional[int], int]]:
        """
        Analyze which families leak into wrong top-5 spots.

        For each true family, count how many times each wrong family
        appears in the top-5.

        Returns:
            Dict[true_family, Dict[contaminating_family, count]]
        """
        contamination = defaultdict(lambda: defaultdict(int))

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            true_family = gt["family_id"]
            top_5 = pred["top_5_families"]

            # Record all families in top-5 that don't match true family
            for match in top_5:
                pred_fam = match["family"]
                if pred_fam != true_family:
                    contamination[true_family][pred_fam] += 1

        return {true: dict(contam) for true, contam in contamination.items()}

    def false_positive_frequencies(self) -> dict[Optional[int], int]:
        """
        Count how many times each family was wrongly matched (false positive).

        Returns:
            Dict[predicted_family, count of false positives]
        """
        fp_counts = defaultdict(int)

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            true_family = gt["family_id"]
            top_5 = pred["top_5_families"]

            if not top_5:
                continue

            pred_family = top_5[0]["family"]

            # Count as false positive if:
            # 1. Not a decoy and predicted wrong family
            # 2. Decoy and predicted something confident
            if gt["is_decoy"]:
                sim = top_5[0]["similarity"]
                if sim >= 0.5:
                    fp_counts[pred_family] += 1
            elif pred_family != true_family:
                fp_counts[pred_family] += 1

        return dict(fp_counts)

    def family_substitution_stats(self, top_n: int = 10) -> list[dict[str, Any]]:
        """
        Identify top N recurring wrong-family replacements.

        Returns:
            List of {true_family, wrong_family, count}, sorted by count desc
        """
        substitutions = defaultdict(int)

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            true_family = gt["family_id"]

            if gt["is_decoy"]:
                continue

            top_5 = pred["top_5_families"]
            if not top_5:
                continue

            pred_family = top_5[0]["family"]
            if pred_family != true_family:
                key = (true_family, pred_family)
                substitutions[key] += 1

        # Convert to list and sort
        result = [
            {
                "true_family": key[0],
                "wrong_family": key[1],
                "count": count,
            }
            for key, count in substitutions.items()
        ]
        result.sort(key=lambda x: x["count"], reverse=True)
        return result[:top_n]

    def precision_decay_by_family(self) -> dict[Optional[int], dict[str, Any]]:
        """
        Compute precision@5 broken down by true family.

        For each true family, compute:
        - num_incidents: count
        - correct_rank1: count of times ranked 1st
        - precision@5: mean precision across top-5
        - family_precision: num_correct / num_incidents

        Returns:
            Dict[family, {num_incidents, correct_rank1, precision@5, family_precision}]
        """
        family_stats = defaultdict(
            lambda: {
                "num_incidents": 0,
                "correct_rank1": 0,
                "correct_in_top5": 0,
                "precision_sum": 0.0,
            }
        )

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            true_family = gt["family_id"]

            # Skip decoys
            if gt["is_decoy"]:
                continue

            top_5 = pred["top_5_families"]
            stats = family_stats[true_family]
            stats["num_incidents"] += 1

            if top_5 and top_5[0]["family"] == true_family:
                stats["correct_rank1"] += 1

            # Count correct in top-5
            correct_in_top5 = sum(1 for f in top_5 if f["family"] == true_family)
            if correct_in_top5 > 0:
                stats["correct_in_top5"] += 1

            # Precision: hits / k
            hits = correct_in_top5
            stats["precision_sum"] += (hits / min(5, len(top_5))) if top_5 else 0.0

        # Compute final metrics
        result = {}
        for family, stats in family_stats.items():
            n = stats["num_incidents"]
            if n > 0:
                result[family] = {
                    "num_incidents": n,
                    "correct_rank1": stats["correct_rank1"],
                    "correct_in_top5": stats["correct_in_top5"],
                    "recall": stats["correct_in_top5"] / n,
                    "precision@5": round(stats["precision_sum"] / n, 4),
                    "rank1_accuracy": stats["correct_rank1"] / n,
                }

        return result

    def remediation_mismatches(self) -> dict[str, Any]:
        """
        Analyze remediation failures.

        Returns:
            {
              total_with_expected_remediation,
              total_with_suggested_remediation,
              exact_matches,
              wrong_action,
              missing_action,
              false_confidence,
              confidence_above_threshold_but_wrong
            }
        """
        stats = {
            "total_with_expected_remediation": 0,
            "total_with_suggested_remediation": 0,
            "exact_matches": 0,
            "wrong_action": 0,
            "missing_action": 0,
            "false_confidence": 0,
            "confidence_above_threshold_but_wrong": 0,
            "mismatches_by_family": defaultdict(int),
        }

        for diag in self.incidents:
            gt = diag["ground_truth"]
            pred = diag["prediction"]

            expected = gt["expected_remediation"]
            if expected is None:
                continue

            stats["total_with_expected_remediation"] += 1

            top_remed = pred["top_remediation_action"]
            if top_remed is None:
                stats["missing_action"] += 1
            else:
                stats["total_with_suggested_remediation"] += 1
                if top_remed["action"] == expected:
                    stats["exact_matches"] += 1
                else:
                    stats["wrong_action"] += 1
                    stats["mismatches_by_family"][gt["family_id"]] += 1

                    if top_remed["confidence"] >= 0.5:
                        stats["confidence_above_threshold_but_wrong"] += 1

        stats["mismatches_by_family"] = dict(stats["mismatches_by_family"])
        return stats

    def decoy_failure_analysis(self) -> dict[str, Any]:
        """
        Analyze decoy rejection performance.

        Decoys should return no confident matches (sim < 0.5).

        Returns:
            {
              total_decoys,
              correctly_rejected,
              false_positives,
              avg_top_similarity_rejected,
              avg_top_similarity_fp,
              confidence_threshold_violations
            }
        """
        stats = {
            "total_decoys": 0,
            "correctly_rejected": 0,
            "false_positives": 0,
            "rejected_similarities": [],
            "fp_similarities": [],
            "confidence_threshold_violations": 0,
        }

        for diag in self.incidents:
            gt = diag["ground_truth"]
            if not gt["is_decoy"]:
                continue

            stats["total_decoys"] += 1
            top_5 = diag["prediction"]["top_5_families"]

            if not top_5:
                stats["correctly_rejected"] += 1
                stats["rejected_similarities"].append(0.0)
            else:
                top_sim = top_5[0]["similarity"]
                if top_sim >= 0.5:
                    stats["false_positives"] += 1
                    stats["fp_similarities"].append(top_sim)
                    stats["confidence_threshold_violations"] += 1
                else:
                    stats["correctly_rejected"] += 1
                    stats["rejected_similarities"].append(top_sim)

        # Compute averages
        result = {
            "total_decoys": stats["total_decoys"],
            "correctly_rejected": stats["correctly_rejected"],
            "false_positives": stats["false_positives"],
            "decoy_recall": (
                stats["correctly_rejected"] / max(stats["total_decoys"], 1)
            ),
            "avg_similarity_when_rejected": (
                sum(stats["rejected_similarities"])
                / max(len(stats["rejected_similarities"]), 1)
                if stats["rejected_similarities"]
                else 0.0
            ),
            "avg_similarity_when_fp": (
                sum(stats["fp_similarities"]) / max(len(stats["fp_similarities"]), 1)
                if stats["fp_similarities"]
                else 0.0
            ),
            "false_positive_rate": (
                stats["false_positives"] / max(stats["total_decoys"], 1)
            ),
        }

        return result

    def export_json(self, filename: str) -> None:
        """Export all failure analysis to JSON."""
        analysis = {
            "metadata": self.metadata,
            "confusion_matrix": self.compute_confusion_matrix(),
            "contamination_matrix": self.compute_contamination_matrix(),
            "false_positive_frequencies": self.false_positive_frequencies(),
            "top_substitutions": self.family_substitution_stats(top_n=10),
            "precision_by_family": self.precision_decay_by_family(),
            "remediation_analysis": self.remediation_mismatches(),
            "decoy_analysis": self.decoy_failure_analysis(),
        }

        with open(filename, "w") as f:
            json.dump(analysis, f, indent=2, default=str)
