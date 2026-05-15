"""
remediation_optimizer.py — Remediation action ranking and confidence scoring.

Aggregates historical success rates from similar past incidents.
Computes Bayesian-ish posterior confidence for candidate remediations.
Ensures robust remediation_acc metric through family-level aggregation.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean, median
from typing import Any, Optional


class RemediationOptimizer:
    """
    Optimizes remediation action ranking based on historical success data.

    Strategy:
    1. For each candidate remediation action, aggregate success rates
       from all matching incidents with that action
    2. Weight by similarity to current incident
    3. Apply Bayesian posterior (Beta prior on success rate)
    4. Confidence scaled by similarity and historical data volume
    5. Return top-3 actions with confidence

    Metrics:
    - remediation_acc: Fraction of top-1 actions that match ground truth
    """

    def __init__(
        self,
        prior_success_rate: float = 0.5,
        min_history_count: int = 2,
    ):
        """
        Initialize optimizer.

        Args:
            prior_success_rate: Prior belief in action success (Beta prior alpha)
            min_history_count: Min number of historical matches for credibility
        """
        self.prior_success_rate = prior_success_rate
        self.min_history_count = min_history_count

    def score_remediation_family(
        self,
        action: str,
        family_matches: list[dict],
        graph: Optional[Any] = None,
    ) -> float:
        """
        Score a remediation action based on success in similar incidents.

        Args:
            action: Remediation action (e.g., "restart_service")
            family_matches: Incident matches with similarity scores
            graph: OperationalGraph for historical outcome lookup (optional)

        Returns:
            Confidence score in [0.0, 1.0]
        """
        if not family_matches:
            return 0.0

        # Extract outcomes for this action from family
        successes = 0
        total = 0
        similarity_sum = 0.0

        for match in family_matches:
            # Get remediation info
            rem_action = match.get("remediation_action", "")
            rem_outcome = match.get("remediation_outcome", "")

            if rem_action == action:
                total += 1
                sim = float(match.get("similarity", 0.0))
                similarity_sum += sim

                if rem_outcome == "resolved":
                    successes += 1

        # Not enough data for this action
        if total < self.min_history_count:
            return 0.0

        # Compute empirical success rate
        empirical_rate = successes / total if total > 0 else 0.0

        # Weighted by mean similarity
        avg_similarity = similarity_sum / total if total > 0 else 0.0

        # Bayesian posterior: Beta(successes + alpha, failures + beta)
        # Simple version: weight empirical rate by credibility
        confidence = self._compute_posterior_confidence(
            successes=successes,
            total=total,
            similarity_avg=avg_similarity,
        )

        return confidence

    def rank_remediations(
        self,
        similar_matches: list[dict],
        graph: Optional[Any] = None,
        resolver: Optional[Any] = None,
    ) -> list[dict]:
        """
        Rank remediation actions by confidence.

        For each candidate action in similar_matches:
        1. Aggregate success rate across matching incidents
        2. Compute Bayesian confidence
        3. Return top-3 with confidence scores

        Args:
            similar_matches: List of similar incident matches
            graph: OperationalGraph for historical lookup
            resolver: IdentityResolver for canonical service lookup

        Returns:
            [
                {
                    "action": str,
                    "target": str,
                    "confidence": float,
                    "success_count": int,
                    "total_count": int,
                    "historical_outcome": str,
                }
            ]
        """
        if not similar_matches:
            return []

        # Collect all actions with their aggregated stats
        action_stats: dict[str, dict] = defaultdict(
            lambda: {
                "successes": 0,
                "total": 0,
                "targets": Counter(),
                "similarities": [],
            }
        )

        for match in similar_matches:
            action = match.get("remediation_action", "")
            if not action:
                continue

            target = match.get("target", match.get("service", ""))
            outcome = match.get(
                "remediation_outcome",
                "resolved" if match.get("remediation_action") else "",
            )
            sim = float(match.get("similarity", 0.0))

            stats = action_stats[action]
            stats["total"] += 1
            stats["similarities"].append(sim)
            stats["targets"][target] += 1

            if outcome == "resolved":
                stats["successes"] += 1

        # Score each action
        ranked: list[dict] = []
        for action, stats in action_stats.items():
            total = stats["total"]
            successes = stats["successes"]
            similarities = stats["similarities"]

            # Skip low-frequency actions
            if total < self.min_history_count:
                continue

            avg_sim = mean(similarities) if similarities else 0.0
            confidence = self._compute_posterior_confidence(
                successes=successes,
                total=total,
                similarity_avg=avg_sim,
            )

            # Most common target
            target = stats["targets"].most_common(1)[0][0] if stats["targets"] else ""

            ranked.append(
                {
                    "action": action,
                    "target": target,
                    "confidence": round(confidence, 3),
                    "success_count": successes,
                    "total_count": total,
                    "historical_outcome": "resolved"
                    if confidence >= 0.6
                    else "uncertain",
                }
            )

        # Sort by confidence descending
        ranked.sort(key=lambda x: x["confidence"], reverse=True)

        # Return top-3
        return ranked[:3]

    def confidence_for_action(
        self,
        action: str,
        success_count: int,
        total_count: int,
        similarity_avg: float = 0.5,
    ) -> float:
        """
        Compute Bayesian-ish confidence for an action.

        Uses Beta distribution-inspired scoring:
        - Prior: belief in typical action success rate
        - Likelihood: observed successes/failures
        - Posterior: combination scaled by similarity and data volume

        Capped in [0.05, 0.99].

        Args:
            action: Remediation action name
            success_count: Number of successful resolutions
            total_count: Total historical attempts
            similarity_avg: Average similarity of historical matches

        Returns:
            Confidence in [0.05, 0.99]
        """
        return self._compute_posterior_confidence(
            successes=success_count,
            total=total_count,
            similarity_avg=similarity_avg,
        )

    # ================================================================
    # Private helpers
    # ================================================================

    def _compute_posterior_confidence(
        self,
        successes: int,
        total: int,
        similarity_avg: float,
    ) -> float:
        """
        Compute posterior confidence (internal).

        Bayesian Beta posterior:
        - Prior: Beta(alpha=2, beta=2) centered at 0.5
        - Likelihood: observed successes/failures
        - Posterior: (alpha + successes) / (alpha + beta + total)

        Scaled by:
        - Data credibility (volume + similarity)
        - Historical trend confidence

        Args:
            successes: Successes observed
            total: Total attempts
            similarity_avg: Mean similarity of historical matches

        Returns:
            Confidence in [0.05, 0.99]
        """
        if total <= 0:
            return 0.05

        # Beta prior: Beta(alpha=2, beta=2) ≈ uniform but slightly centered
        alpha = 2.0
        beta = 2.0

        # Posterior mean
        empirical_rate = successes / total
        posterior_mean = (alpha + successes) / (alpha + beta + total)

        # Credibility adjustment: more data + higher similarity = higher confidence
        credibility = min(1.0, total / 10.0)  # Saturates at 10 datapoints
        similarity_weight = similarity_avg  # Higher similarity = more weight

        # Final confidence: blend empirical with posterior, weighted by credibility
        base_confidence = (
            (empirical_rate * 0.4 + posterior_mean * 0.6)
            * credibility
            * similarity_weight
        )

        # Cap at [0.05, 0.99]
        return round(max(0.05, min(0.99, base_confidence)), 3)

    def _aggregate_family_outcomes(
        self,
        family_matches: list[dict],
    ) -> dict[str, Any]:
        """
        Aggregate outcomes at family level.

        Args:
            family_matches: Incident matches from same family

        Returns:
            {
                "family_id": str,
                "success_rate": float,
                "total_count": int,
                "actions": {action: count, ...},
            }
        """
        if not family_matches:
            return {
                "family_id": "unknown",
                "success_rate": 0.0,
                "total_count": 0,
                "actions": {},
            }

        family_id = family_matches[0].get("incident_id", "unknown").rsplit("-", 1)[-1]

        successes = sum(
            1 for m in family_matches if m.get("remediation_outcome") == "resolved"
        )
        total = len(family_matches)

        actions = Counter(
            m.get("remediation_action", "")
            for m in family_matches
            if m.get("remediation_action")
        )

        return {
            "family_id": family_id,
            "success_rate": round(successes / total if total > 0 else 0.0, 3),
            "total_count": total,
            "actions": dict(actions),
        }


# ============================================================================
# Integration helpers
# ============================================================================


def rank_remediations_simple(
    similar_matches: list[dict],
) -> list[dict]:
    """
    Standalone function to rank remediations from similar matches.

    Args:
        similar_matches: List of incident matches with remediation info

    Returns:
        Ranked remediations with confidence
    """
    optimizer = RemediationOptimizer()
    return optimizer.rank_remediations(similar_matches)


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example: similar matches with remediation history
    matches = [
        {
            "incident_id": "INC-X-1",
            "similarity": 0.85,
            "remediation_action": "restart_service",
            "remediation_outcome": "resolved",
            "target": "svc-A",
        },
        {
            "incident_id": "INC-X-2",
            "similarity": 0.80,
            "remediation_action": "restart_service",
            "remediation_outcome": "resolved",
            "target": "svc-A",
        },
        {
            "incident_id": "INC-X-3",
            "similarity": 0.75,
            "remediation_action": "restart_service",
            "remediation_outcome": "failed",
            "target": "svc-A",
        },
        {
            "incident_id": "INC-Y-1",
            "similarity": 0.70,
            "remediation_action": "scale_up",
            "remediation_outcome": "resolved",
            "target": "svc-B",
        },
    ]

    optimizer = RemediationOptimizer()
    ranked = optimizer.rank_remediations(matches)

    print("Ranked remediations:")
    for i, rem in enumerate(ranked):
        print(
            f"  {i + 1}. {rem['action']} (target={rem['target']}) "
            f"confidence={rem['confidence']}, "
            f"success={rem['success_count']}/{rem['total_count']}"
        )
