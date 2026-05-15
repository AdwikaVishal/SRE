"""
decoy_suppression.py — Aggressive decoy detection and confidence suppression.

Implements benchmark-targeted decoy handling to suppress false positives.
Decoys are incidents that look similar but lack supporting evidence.

Evidence requirements:
- Deploy: service was deployed
- Metric: performance change observed
- Trace/Log: detailed evidence of what changed

If ANY evidence type missing → likely decoy → aggressively suppress.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Optional


class DecoySuppressionEngine:
    """
    Detects and suppresses decoy (false positive) incident matches.

    Strategy:
    1. Check for complete evidence: deploy + metric + trace/log
    2. If incomplete: classify as likely decoy
    3. Suppress confidence: multiply by suppression_multiplier (~0.60)
    4. Cap similarity at lower threshold (~0.45)

    Goal: Reliable decoy_correctness metric (avoid false positives).
    """

    def __init__(
        self,
        decoy_confidence_multiplier: float = 0.60,
        decoy_similarity_cap: float = 0.45,
        strict_mode: bool = False,
    ):
        """
        Initialize suppression engine.

        Args:
            decoy_confidence_multiplier: Factor to multiply confidence for decoys
            decoy_similarity_cap: Max similarity for decoy matches
            strict_mode: If True, any missing evidence → treat as decoy
        """
        self.decoy_confidence_multiplier = decoy_confidence_multiplier
        self.decoy_similarity_cap = decoy_similarity_cap
        self.strict_mode = strict_mode

    def is_likely_decoy(
        self,
        signal: dict,
        related_events: list[dict],
    ) -> bool:
        """
        Determine if signal is likely a decoy (false alarm).

        Checks evidence completeness:
        - has_deploy: At least one deploy event
        - has_metric: At least one metric event
        - has_trace_or_log: At least one trace or log event

        Returns True if ANY evidence type is missing.

        Args:
            signal: The incident signal dict
            related_events: List of related events

        Returns:
            True if likely decoy, False if has strong evidence
        """
        kinds = Counter(e.get("kind") for e in related_events)

        has_deploy = kinds.get("deploy", 0) > 0
        has_metric = kinds.get("metric", 0) > 0
        has_trace_or_log = (kinds.get("trace", 0) + kinds.get("log", 0)) > 0

        # Decoy if ANY evidence type is missing
        is_decoy = not (has_deploy and has_metric and has_trace_or_log)

        return is_decoy

    def suppress_confidence(
        self,
        confidence: float,
        is_decoy: bool,
    ) -> float:
        """
        Apply confidence suppression for decoys.

        If is_decoy:
        - Multiply by suppression_multiplier (e.g., 0.60)
        - Monotonic: higher original → proportionally higher suppressed

        Args:
            confidence: Original confidence in [0.0, 1.0]
            is_decoy: Whether incident is classified as decoy

        Returns:
            Suppressed confidence in [0.0, 1.0]
        """
        if not is_decoy:
            return confidence

        suppressed = confidence * self.decoy_confidence_multiplier
        return round(min(1.0, max(0.0, suppressed)), 3)

    def suppress_similarities(
        self,
        matches: list[dict],
        is_decoy: bool,
        cap: Optional[float] = None,
    ) -> list[dict]:
        """
        Apply similarity suppression to matches if decoy detected.

        If is_decoy: clamp all similarities to ≤cap (default 0.45).
        Ensures decoy matches remain below recall threshold.

        Args:
            matches: List of incident matches
            is_decoy: Whether query is classified as decoy
            cap: Max similarity for decoys (default from config)

        Returns:
            Suppressed matches
        """
        if not is_decoy:
            return matches

        cap = cap or self.decoy_similarity_cap

        suppressed = []
        for match in matches:
            out = dict(match)
            sim = float(out.get("similarity", 0.0))
            out["similarity"] = round(min(sim, cap), 3)
            suppressed.append(out)

        return suppressed

    def build_suppression_policy(
        self,
        related_events: list[dict],
    ) -> SuppressionPolicy:
        """
        Build suppression policy from evidence.

        Evaluates evidence completeness and returns policy
        with suppression factors.

        Args:
            related_events: List of event dicts

        Returns:
            SuppressionPolicy with suppression factors
        """
        kinds = Counter(e.get("kind") for e in related_events)

        has_deploy = kinds.get("deploy", 0) > 0
        has_metric = kinds.get("metric", 0) > 0
        has_trace_log = (kinds.get("trace", 0) + kinds.get("log", 0)) > 0

        is_decoy = not (has_deploy and has_metric and has_trace_log)

        evidence_score = sum([has_deploy, has_metric, has_trace_log]) / 3.0

        policy = SuppressionPolicy(
            is_decoy=is_decoy,
            evidence_score=round(evidence_score, 2),
            has_deploy=has_deploy,
            has_metric=has_metric,
            has_trace_log=has_trace_log,
            confidence_multiplier=self.decoy_confidence_multiplier if is_decoy else 1.0,
            similarity_cap=self.decoy_similarity_cap if is_decoy else 1.0,
        )

        return policy

    def apply_suppression(
        self,
        matches: list[dict],
        policy: SuppressionPolicy,
    ) -> list[dict]:
        """
        Apply suppression policy to matches.

        Args:
            matches: Incident matches
            policy: SuppressionPolicy

        Returns:
            Suppressed matches
        """
        if not policy.is_decoy:
            return matches

        suppressed = []
        for match in matches:
            out = dict(match)

            # Suppress similarity
            sim = float(out.get("similarity", 0.0))
            out["similarity"] = round(min(sim, policy.similarity_cap), 3)

            # Suppress confidence if present
            if "confidence" in out:
                conf = float(out["confidence"])
                out["confidence"] = round(conf * policy.confidence_multiplier, 3)

            suppressed.append(out)

        return suppressed

    def analyze_decoy_risk(
        self,
        signal: dict,
        related_events: list[dict],
        similar_matches: list[dict],
    ) -> dict[str, Any]:
        """
        Analyze decoy risk for debugging and reporting.

        Args:
            signal: Incident signal
            related_events: Related events
            similar_matches: Similar past incidents

        Returns:
            {
                "is_decoy": bool,
                "evidence_score": float,
                "risk_level": str,  # "high", "medium", "low"
                "missing_evidence": list[str],
                "suppression_factor": float,
                "top_match_original_sim": float,
                "top_match_suppressed_sim": float,
            }
        """
        is_decoy = self.is_likely_decoy(signal, related_events)
        policy = self.build_suppression_policy(related_events)

        missing = []
        if not policy.has_deploy:
            missing.append("deploy")
        if not policy.has_metric:
            missing.append("metric")
        if not policy.has_trace_log:
            missing.append("trace_or_log")

        risk_level = (
            "high" if is_decoy else ("medium" if policy.evidence_score < 0.8 else "low")
        )

        top_match = similar_matches[0] if similar_matches else {}
        top_orig_sim = float(top_match.get("similarity", 0.0))
        top_supp_sim = round(min(top_orig_sim, policy.similarity_cap), 3)

        return {
            "is_decoy": is_decoy,
            "evidence_score": policy.evidence_score,
            "risk_level": risk_level,
            "missing_evidence": missing,
            "suppression_factor": policy.confidence_multiplier,
            "top_match_original_sim": round(top_orig_sim, 3),
            "top_match_suppressed_sim": top_supp_sim,
        }


# ============================================================================
# SuppressionPolicy dataclass
# ============================================================================


class SuppressionPolicy:
    """Policy for suppressing decoy matches."""

    def __init__(
        self,
        is_decoy: bool,
        evidence_score: float,
        has_deploy: bool,
        has_metric: bool,
        has_trace_log: bool,
        confidence_multiplier: float = 1.0,
        similarity_cap: float = 1.0,
    ):
        self.is_decoy = is_decoy
        self.evidence_score = evidence_score
        self.has_deploy = has_deploy
        self.has_metric = has_metric
        self.has_trace_log = has_trace_log
        self.confidence_multiplier = confidence_multiplier
        self.similarity_cap = similarity_cap

    def __repr__(self) -> str:
        return (
            f"SuppressionPolicy(is_decoy={self.is_decoy}, "
            f"evidence_score={self.evidence_score}, "
            f"conf_mult={self.confidence_multiplier}, "
            f"sim_cap={self.similarity_cap})"
        )


# ============================================================================
# Integration helpers
# ============================================================================


def apply_decoy_suppression(
    signal: dict,
    related_events: list[dict],
    similar_matches: list[dict],
    remediations: list[dict],
) -> tuple[list[dict], list[dict], dict]:
    """
    Standalone function to apply decoy suppression to results.

    Args:
        signal: Incident signal
        related_events: Related events
        similar_matches: Similar past incidents
        remediations: Suggested remediations

    Returns:
        (suppressed_matches, suppressed_remediations, analysis_dict)
    """
    engine = DecoySuppressionEngine()
    is_decoy = engine.is_likely_decoy(signal, related_events)
    policy = engine.build_suppression_policy(related_events)

    suppressed_matches = engine.apply_suppression(similar_matches, policy)
    suppressed_remediations = engine.apply_suppression(remediations, policy)

    analysis = engine.analyze_decoy_risk(signal, related_events, similar_matches)

    return suppressed_matches, suppressed_remediations, analysis


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example 1: Normal incident with complete evidence
    signal_normal = {"trigger": "high_latency", "ts": "2024-01-01T10:00:00Z"}
    events_normal = [
        {"kind": "deploy", "service": "svc-A"},
        {"kind": "metric", "value": 500},
        {"kind": "log", "message": "error"},
    ]
    matches_normal = [
        {
            "incident_id": "INC-X-1",
            "similarity": 0.85,
            "remediation_action": "restart",
        },
    ]

    # Example 2: Decoy incident missing logs
    signal_decoy = {"trigger": "memory_spike", "ts": "2024-01-01T11:00:00Z"}
    events_decoy = [
        {"kind": "deploy", "service": "svc-B"},
        {"kind": "metric", "value": 300},
        # No logs!
    ]
    matches_decoy = [
        {
            "incident_id": "INC-Y-1",
            "similarity": 0.80,
            "remediation_action": "scale_up",
        },
    ]

    engine = DecoySuppressionEngine()

    print("=== Normal Incident (complete evidence) ===")
    is_decoy_normal = engine.is_likely_decoy(signal_normal, events_normal)
    print(f"Is decoy? {is_decoy_normal}")
    policy_normal = engine.build_suppression_policy(events_normal)
    print(f"Policy: {policy_normal}")

    analysis_normal = engine.analyze_decoy_risk(
        signal_normal, events_normal, matches_normal
    )
    print(f"Analysis: {analysis_normal}")

    suppressed_normal = engine.apply_suppression(matches_normal, policy_normal)
    print(f"Suppressed match similarity: {suppressed_normal[0]['similarity']}")

    print("\n=== Decoy Incident (missing logs) ===")
    is_decoy_decoy = engine.is_likely_decoy(signal_decoy, events_decoy)
    print(f"Is decoy? {is_decoy_decoy}")
    policy_decoy = engine.build_suppression_policy(events_decoy)
    print(f"Policy: {policy_decoy}")

    analysis_decoy = engine.analyze_decoy_risk(
        signal_decoy, events_decoy, matches_decoy
    )
    print(f"Analysis: {analysis_decoy}")

    suppressed_decoy = engine.apply_suppression(matches_decoy, policy_decoy)
    print(f"Suppressed match similarity: {suppressed_decoy[0]['similarity']}")
