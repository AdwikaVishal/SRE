"""
diagnostic_extractor.py — Incident-level diagnostic extraction.

Extracts deterministic, per-incident diagnostic data suitable for analysis.
Records ground truth, predictions, scoring details, and evidence tracking.
"""

from __future__ import annotations

from typing import Any, Optional


class DiagnosticExtractor:
    """Extracts incident-level diagnostics from benchmark run data."""

    @staticmethod
    def dump_incident_diagnostics(
        seed: int,
        incident_idx: int,
        ground_truth: dict[str, Any],
        prediction: dict[str, Any],
        related_events: list[dict[str, Any]],
        similar_matches: list[dict[str, Any]],
        remediations: list[dict[str, Any]],
        latency_ms: float,
    ) -> dict[str, Any]:
        """
        Extract deterministic incident-level diagnostics.

        Args:
            seed: Random seed for dataset generation
            incident_idx: Index in the eval signal list
            ground_truth: GT dict with 'family', 'expected_remediation', etc.
            prediction: Context output with similar_past_incidents, suggested_remediations
            related_events: Events in the causal context
            similar_matches: Top matches returned by engine (already top-5 filtered)
            remediations: Suggested remediations from engine
            latency_ms: Query latency in milliseconds

        Returns:
            Structured diagnostic dict suitable for JSON/CSV export
        """
        gt_family = ground_truth.get("family")
        is_decoy = gt_family is None
        incident_id = ground_truth.get("incident_id", f"incident-{incident_idx}")
        canonical_service_id = ground_truth.get("canonical_trigger_service", "")

        # Extract top-5 predictions with family extraction
        top_5_families = []
        for i, match in enumerate(similar_matches[:5]):
            pred_family = _extract_family_from_incident_id(match.get("incident_id", ""))
            top_5_families.append(
                {
                    "rank": i + 1,
                    "incident_id": match.get("incident_id", ""),
                    "family": pred_family,
                    "similarity": float(match.get("similarity", 0.0)),
                    "rationale": match.get("rationale", ""),
                }
            )

        # Top remediation action
        top_remediation = None
        if remediations:
            top = remediations[0]
            top_remediation = {
                "action": top.get("action", ""),
                "target": top.get("target", ""),
                "confidence": float(top.get("confidence", 0.0)),
                "historical_outcome": top.get("historical_outcome", ""),
            }

        # Ground truth remediation
        expected_remediation = ground_truth.get("expected_remediation")

        # Graph evidence tracking
        graph_evidence = _analyze_graph_evidence(related_events)

        # Scoring attribution: why did top candidate win?
        scoring_attribution = _compute_scoring_attribution(
            top_5_families, gt_family, is_decoy
        )

        return {
            "meta": {
                "seed": seed,
                "incident_idx": incident_idx,
                "incident_id": incident_id,
                "query_latency_ms": round(latency_ms, 2),
            },
            "ground_truth": {
                "family_id": gt_family,
                "is_decoy": is_decoy,
                "canonical_service_id": canonical_service_id,
                "expected_remediation": expected_remediation,
            },
            "prediction": {
                "top_5_families": top_5_families,
                "top_remediation_action": top_remediation,
                "num_confident_matches": len(
                    [
                        m
                        for m in similar_matches
                        if float(m.get("similarity", 0.0)) >= 0.5
                    ]
                ),
            },
            "graph_evidence": graph_evidence,
            "scoring_attribution": scoring_attribution,
        }


def _extract_family_from_incident_id(incident_id: str) -> Optional[int]:
    """Extract family ID from incident_id format like 'incident-123-5' where 5 is family."""
    try:
        parts = incident_id.rsplit("-", 1)
        if len(parts) == 2:
            return int(parts[-1])
    except (ValueError, IndexError, AttributeError):
        pass
    return None


def _analyze_graph_evidence(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Analyze what types of evidence are present in the event graph."""
    has_deploy = False
    has_metric = False
    has_trace_or_log = False
    has_remediation = False
    event_kinds = {}

    for evt in events:
        kind = evt.get("kind", "")
        event_kinds[kind] = event_kinds.get(kind, 0) + 1

        if kind == "deploy":
            has_deploy = True
        elif kind == "metric":
            has_metric = True
        elif kind in ("trace", "log"):
            has_trace_or_log = True
        elif kind == "remediation":
            has_remediation = True

    return {
        "has_deploy": has_deploy,
        "has_metric": has_metric,
        "has_trace_or_log": has_trace_or_log,
        "has_remediation": has_remediation,
        "total_events": len(events),
        "event_kinds_breakdown": event_kinds,
    }


def _compute_scoring_attribution(
    top_5_families: list[dict[str, Any]],
    true_family: Optional[int],
    is_decoy: bool,
) -> dict[str, Any]:
    """Determine why the top candidate won and whether it was correct."""
    if not top_5_families:
        return {
            "top_candidate_family": None,
            "top_candidate_similarity": 0.0,
            "match_outcome": "no_candidates" if not is_decoy else "correct_no_match",
        }

    top = top_5_families[0]
    top_family = top["family"]
    top_sim = top["similarity"]

    if is_decoy:
        # For decoys, success = no confident match (sim < 0.5)
        if top_sim >= 0.5:
            return {
                "top_candidate_family": top_family,
                "top_candidate_similarity": top_sim,
                "match_outcome": "false_positive_decoy",
            }
        else:
            return {
                "top_candidate_family": top_family,
                "top_candidate_similarity": top_sim,
                "match_outcome": "correct_decoy_rejection",
            }

    # Normal incident (not decoy)
    is_correct = top_family == true_family
    if is_correct:
        return {
            "top_candidate_family": top_family,
            "top_candidate_similarity": top_sim,
            "match_outcome": "correct_rank1",
        }
    else:
        return {
            "top_candidate_family": top_family,
            "true_family": true_family,
            "top_candidate_similarity": top_sim,
            "match_outcome": "wrong_rank1_recall_miss",
        }
