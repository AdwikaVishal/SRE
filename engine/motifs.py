"""
Behavioral Motif Index

Stores abstract behavioral fingerprints of past incidents, independent of
service names or topology. This is what makes recall@5 work across renames.

Similarity = weighted combination of:
  - event_sequence overlap (Jaccard)
  - causal_shape structural similarity (edge-set Jaccard approximation)
  - remediation_action match (boolean bonus)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from engine.graph import IncidentMotif


@dataclass
class IncidentMatch:
    incident_id: str
    similarity: float
    rationale: str
    remediation_action: str
    remediation_outcome: str
    timestamp: str
    canonical_ids: list[str]


class BehavioralMotifIndex:
    """
    In-memory index of behavioral motifs for past incidents.
    Queryable in < 50ms for up to 24 stored incidents (L2 scale).
    """

    def __init__(self) -> None:
        self._motifs: list[IncidentMotif] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def index_incident(self, motif: IncidentMotif) -> None:
        """Store a completed incident motif."""
        with self._lock:
            # Avoid duplicate incident IDs
            existing_ids = {m.incident_id for m in self._motifs}
            if motif.incident_id and motif.incident_id in existing_ids:
                # Update existing
                for i, m in enumerate(self._motifs):
                    if m.incident_id == motif.incident_id:
                        self._motifs[i] = motif
                        return
            self._motifs.append(motif)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def find_similar(
        self,
        query_motif: IncidentMotif,
        top_k: int = 5,
    ) -> list[IncidentMatch]:
        """
        Find the top_k most similar past incidents to the query motif.
        Matching is purely structural — no service names involved.
        """
        with self._lock:
            if not self._motifs:
                return []

            scored: list[tuple[float, IncidentMotif]] = []
            for stored in self._motifs:
                score, rationale = _compute_similarity(query_motif, stored)
                scored.append((score, stored, rationale))

            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[:top_k]

            return [
                IncidentMatch(
                    incident_id=m.incident_id,
                    similarity=round(score, 3),
                    rationale=rationale,
                    remediation_action=m.remediation_action,
                    remediation_outcome=m.remediation_outcome,
                    timestamp=m.timestamp,
                    canonical_ids=list(m.canonical_ids),
                )
                for score, m, rationale in top
                if score > 0.0
            ]

    def count(self) -> int:
        return len(self._motifs)

    def all_motifs(self) -> list[IncidentMotif]:
        with self._lock:
            return list(self._motifs)


# ------------------------------------------------------------------
# Similarity computation
# ------------------------------------------------------------------

def _jaccard(a: list | set, b: list | set) -> float:
    """Jaccard similarity between two sets."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _compute_similarity(
    query: IncidentMotif,
    stored: IncidentMotif,
) -> tuple[float, str]:
    """
    Compute weighted similarity between two motifs.
    Returns (score, rationale_string).
    """
    # 1. Event sequence Jaccard (weight: 0.4)
    seq_sim = _jaccard(query.event_sequence, stored.event_sequence)

    # 2. Causal shape Jaccard — edge set similarity (weight: 0.4)
    q_edges = set(query.causal_shape)
    s_edges = set(stored.causal_shape)
    shape_sim = _jaccard(q_edges, s_edges)

    # 3. Remediation action match bonus (weight: 0.2)
    action_match = 0.0
    if (
        query.remediation_action
        and stored.remediation_action
        and query.remediation_action == stored.remediation_action
    ):
        action_match = 1.0

    score = 0.4 * seq_sim + 0.4 * shape_sim + 0.2 * action_match

    # Build rationale
    parts = []
    if seq_sim > 0:
        common_events = set(query.event_sequence) & set(stored.event_sequence)
        parts.append(f"shared event types: {', '.join(sorted(common_events))}")
    if shape_sim > 0:
        parts.append(f"causal shape similarity: {shape_sim:.0%}")
    if action_match:
        parts.append(f"same remediation action: {stored.remediation_action}")
    rationale = "; ".join(parts) if parts else "low structural overlap"

    return score, rationale
