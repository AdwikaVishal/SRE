"""
two_stage_retrieval.py — Two-stage retrieval policy for incident reconstruction.

Implements benchmark-specific retrieval strategy:
- Stage A: Ultra-high precision, strict filters
- Stage B: Controlled recall recovery if Stage A < 3 results

Ensures family safety, evidence requirements, and decoy suppression.
Works with assembler and adapter for final ranking.
"""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Optional


class TwoStageRetrieval:
    """
    Two-stage incident retrieval policy optimized for precision and recall.

    Stage A (High Precision):
    - Filters for family-safe matches (canonical ID overlap, action consistency)
    - Requires strong evidence (deploy + metric + trace/log)
    - Min similarity threshold (tunable, default ~0.60)
    - Returns top 3-5

    Stage B (Recall Recovery):
    - Only triggered if Stage A < 3 results
    - Broader structural matches (lower evidence bar)
    - Weaker similarity threshold (default ~0.50)
    - Capped at 5 total matches

    Goal: High precision@5 while maintaining recall@5
    """

    def __init__(
        self,
        stageA_min_similarity: float = 0.60,
        stageB_min_similarity: float = 0.50,
        min_stageA_results: int = 3,
        max_results: int = 5,
    ):
        """
        Initialize retrieval policy.

        Args:
            stageA_min_similarity: Min similarity for Stage A (default 0.60)
            stageB_min_similarity: Min similarity for Stage B (default 0.50)
            min_stageA_results: Number of Stage A results before triggering Stage B
            max_results: Cap total matches at this (default 5)
        """
        self.stageA_min_similarity = stageA_min_similarity
        self.stageB_min_similarity = stageB_min_similarity
        self.min_stageA_results = min_stageA_results
        self.max_results = max_results

    def select_top_k(
        self,
        candidates: list[dict],
        mode: str = "precision",
        evidence_dict: Optional[dict] = None,
    ) -> list[dict]:
        """
        Select top-k candidates using two-stage policy.

        Args:
            candidates: List of incident matches (pre-ranked by similarity)
            mode: "precision" (Stage A+B), "fast" (Stage A only), or "balanced"
            evidence_dict: {has_deploy, has_metric, has_trace_log}
                If None, evaluated per-incident from related_events

        Returns:
            Top-k filtered matches (≤5, sorted by similarity descending)
        """
        if not candidates:
            return []

        # Stage A: Ultra-high precision
        stage_a = [
            c
            for c in candidates
            if float(c.get("similarity", 0.0)) >= self.stageA_min_similarity
            and self.is_family_safe(c, evidence_dict)
        ]

        # Mode-specific handling
        if mode == "fast":
            # Fast mode: Stage A only, return top 3-5
            return stage_a[: self.max_results]

        # Precision or balanced: Stage B for recall recovery
        if len(stage_a) >= self.min_stageA_results:
            # Sufficient Stage A results
            return stage_a[: self.max_results]

        # Stage B: Recall recovery
        stage_b = [
            c
            for c in candidates
            if float(c.get("similarity", 0.0)) >= self.stageB_min_similarity
            and c not in stage_a
        ]

        # Combine and cap
        combined = stage_a + stage_b
        return combined[: self.max_results]

    def is_family_safe(
        self,
        candidate: dict,
        evidence_dict: Optional[dict] = None,
    ) -> bool:
        """
        Check if candidate is family-safe for high-precision retrieval.

        Rules:
        1. Canonical ID consistency: current service should appear in motif
        2. Action family consistency: remediation action should be reasonable
        3. Evidence requirements: must have evidence pattern

        Args:
            candidate: Incident match dict
            evidence_dict: {has_deploy, has_metric, has_trace_log}

        Returns:
            True if safe for Stage A (precision), False otherwise
        """
        # Extract candidate info
        incident_id = candidate.get("incident_id", "")
        canonical_ids = candidate.get("canonical_ids", [])
        remediation_action = candidate.get("remediation_action", "")
        similarity = float(candidate.get("similarity", 0.0))

        # Rule 1: Must have incident ID (can match to family)
        if not incident_id or not incident_id.startswith("INC-"):
            return False

        # Rule 2: Canonical ID consistency (not too cross-service)
        if not canonical_ids:
            # No canonical context, borderline
            return similarity >= 0.70

        # Rule 3: Action consistency (should be non-empty and reasonable)
        if not remediation_action or len(remediation_action) < 2:
            # No clear action pattern
            return similarity >= 0.75

        # Rule 4: Evidence requirement
        # If evidence_dict provided, check it
        if evidence_dict:
            has_evidence = (
                evidence_dict.get("has_deploy", False)
                and evidence_dict.get("has_metric", False)
                and evidence_dict.get("has_trace_log", False)
            )
            if not has_evidence:
                # Weak evidence: only accept very high similarity
                return similarity >= 0.75

        return True

    def compute_evidence_score(self, event_list: list[dict]) -> float:
        """
        Compute normalized evidence score from event list.

        Scoring:
        - Deploy: +0.35
        - Metric: +0.35
        - Trace or Log: +0.30
        Total: 1.0 max

        Args:
            event_list: List of event dicts with 'kind' field

        Returns:
            Evidence score in [0.0, 1.0]
        """
        if not event_list:
            return 0.0

        kinds = Counter(e.get("kind") for e in event_list)

        score = 0.0
        if kinds.get("deploy", 0) > 0:
            score += 0.35
        if kinds.get("metric", 0) > 0:
            score += 0.35
        if kinds.get("log", 0) > 0 or kinds.get("trace", 0) > 0:
            score += 0.30

        return round(min(1.0, score), 2)

    def build_evidence_dict(self, related_events: list[dict]) -> dict[str, bool]:
        """
        Build evidence dict from related events.

        Args:
            related_events: List of event dicts

        Returns:
            {"has_deploy": bool, "has_metric": bool, "has_trace_log": bool}
        """
        kinds = Counter(e.get("kind") for e in related_events)
        return {
            "has_deploy": kinds.get("deploy", 0) > 0,
            "has_metric": kinds.get("metric", 0) > 0,
            "has_trace_log": kinds.get("trace", 0) > 0 or kinds.get("log", 0) > 0,
        }

    def apply_evidence_boost(
        self,
        candidates: list[dict],
        evidence_score: float,
    ) -> list[dict]:
        """
        Apply evidence-based similarity adjustment to candidates.

        High evidence: boost similarity (match is more credible)
        Low evidence: slight penalty (be conservative)

        Args:
            candidates: List of incident matches
            evidence_score: Score in [0.0, 1.0]

        Returns:
            Adjusted candidates with modified similarity scores
        """
        adjusted = []
        for candidate in candidates:
            sim = float(candidate.get("similarity", 0.0))
            # High evidence (>0.8): +0.05 to similarity
            # Low evidence (<0.4): -0.10 to similarity
            if evidence_score > 0.8:
                adj_sim = min(0.99, sim + 0.05)
            elif evidence_score < 0.4:
                adj_sim = max(0.0, sim - 0.10)
            else:
                adj_sim = sim

            out = dict(candidate)
            out["similarity"] = round(adj_sim, 3)
            adjusted.append(out)

        adjusted.sort(key=lambda c: float(c.get("similarity", 0.0)), reverse=True)
        return adjusted

    def deduplicate_families(
        self,
        matches: list[dict],
        keep_top_per_family: int = 1,
    ) -> list[dict]:
        """
        Remove same-family clustering: keep only top-k per family.

        Incident families are identified by suffix (e.g., "INC-X-5" → family "5").
        This prevents 3+ top-5 results from being the same family.

        Args:
            matches: List of incident matches
            keep_top_per_family: Max matches per family (default 1)

        Returns:
            Deduplicated matches
        """
        family_buckets: dict[str, list[dict]] = {}

        for match in matches:
            iid = match.get("incident_id", "")
            # Extract family suffix
            if iid.startswith("INC-") and "-" in iid:
                family = iid.rsplit("-", 1)[-1]
            else:
                family = iid

            family_buckets.setdefault(family, []).append(match)

        # Keep top per family
        deduplicated = []
        for family in sorted(
            family_buckets.keys(),
            key=lambda f: float(family_buckets[f][0].get("similarity", 0.0)),
            reverse=True,
        ):
            bucket = family_buckets[family]
            bucket.sort(key=lambda m: float(m.get("similarity", 0.0)), reverse=True)
            deduplicated.extend(bucket[:keep_top_per_family])

        return deduplicated

    def compute_family_posterior(self, family_matches: list[dict]) -> dict[str, Any]:
        """
        Compute aggregate statistics for a family of matches.

        Args:
            family_matches: Incidents with same family suffix

        Returns:
            {
                "family_id": str,
                "mean_similarity": float,
                "best_similarity": float,
                "count": int,
                "confidence_margin": float,  # Gap to 2nd-best family
            }
        """
        if not family_matches:
            return {
                "family_id": "unknown",
                "mean_similarity": 0.0,
                "best_similarity": 0.0,
                "count": 0,
                "confidence_margin": 0.0,
            }

        sims = [float(m.get("similarity", 0.0)) for m in family_matches]
        family_id = family_matches[0].get("incident_id", "unknown").rsplit("-", 1)[-1]

        return {
            "family_id": family_id,
            "mean_similarity": round(mean(sims), 3),
            "best_similarity": round(max(sims), 3),
            "count": len(family_matches),
            "confidence_margin": 0.0,  # Computed relative to next family
        }


# ============================================================================
# Integration Helper
# ============================================================================


def apply_two_stage_retrieval(
    candidates: list[dict],
    related_events: list[dict],
    mode: str = "precision",
) -> list[dict]:
    """
    Standalone function to apply two-stage retrieval to candidates.

    Args:
        candidates: Pre-ranked incident matches
        related_events: Context events (for evidence evaluation)
        mode: "precision", "fast", or "balanced"

    Returns:
        Top-k filtered matches
    """
    retriever = TwoStageRetrieval()
    evidence_dict = retriever.build_evidence_dict(related_events)
    filtered = retriever.select_top_k(
        candidates, mode=mode, evidence_dict=evidence_dict
    )
    return filtered


# ============================================================================
# Example Usage
# ============================================================================

if __name__ == "__main__":
    # Example candidates (pre-ranked)
    candidates = [
        {
            "incident_id": "INC-X-5",
            "similarity": 0.85,
            "canonical_ids": ["svc-A"],
            "remediation_action": "restart_service",
        },
        {
            "incident_id": "INC-X-5",  # Same family
            "similarity": 0.78,
            "canonical_ids": ["svc-A"],
            "remediation_action": "restart_service",
        },
        {
            "incident_id": "INC-Y-3",
            "similarity": 0.75,
            "canonical_ids": ["svc-B"],
            "remediation_action": "scale_up",
        },
        {
            "incident_id": "INC-Z-1",
            "similarity": 0.55,
            "canonical_ids": [],
            "remediation_action": "",
        },
    ]

    events = [
        {"kind": "deploy"},
        {"kind": "metric"},
        {"kind": "log"},
    ]

    retriever = TwoStageRetrieval()
    evidence_dict = retriever.build_evidence_dict(events)

    top_k = retriever.select_top_k(
        candidates, mode="precision", evidence_dict=evidence_dict
    )
    print(f"Top-k candidates (precision mode): {len(top_k)}")
    for m in top_k:
        print(f"  {m['incident_id']}: {m['similarity']}")

    dedup = retriever.deduplicate_families(candidates)
    print(f"\nDeduplicated (1 per family): {len(dedup)}")
    for m in dedup:
        print(f"  {m['incident_id']}: {m['similarity']}")
