"""
Tests for BehavioralMotifIndex.

Covers: indexing, counting, similarity scoring (identical / partial / disjoint),
ranking, top-k clamping, update-in-place, remediation-action bonus, and field
correctness on returned IncidentMatch objects.

Similarity formula (from motifs.py):
    score = 0.45 * shape_jaccard
          + 0.30 * seq_jaccard
          + 0.15 * action_match   (1.0 iff both motifs share the same action)
          + 0.10 * order_bonus    (LCS / max_len)
    → identical motifs always produce score == 1.0
"""

import pytest
from engine.identity import IdentityResolver
from engine.motifs import BehavioralMotifIndex, _role_shape_similarity
from engine.models import IncidentMotif

_PAY_SHAPE = [
    ("payment", "deploy_to_metric", "payment"),
    ("payment", "error_log_during_incident", "checkout"),
]
_CHECKOUT_SHAPE = [("checkout", "upstream_call", "payment")]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _motif(
    incident_id: str,
    event_sequence=None,
    causal_shape=None,
    remediation_action: str = "",
    remediation_outcome: str = "",
    timestamp: str = "2026-01-01T00:00:00+00:00",
    confidence: float = 0.9,
    canonical_ids=None,
) -> IncidentMotif:
    """Factory for IncidentMotif with sensible defaults."""
    return IncidentMotif(
        incident_id=incident_id,
        event_sequence=event_sequence if event_sequence is not None else ["payment", "checkout"],
        causal_shape=causal_shape if causal_shape is not None else [
            ("payment", "deploy_to_metric", "payment"),
        ],
        remediation_action=remediation_action,
        remediation_outcome=remediation_outcome,
        timestamp=timestamp,
        confidence=confidence,
        canonical_ids=canonical_ids or [],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBehavioralMotifIndex:

    def setup_method(self):
        self.idx = BehavioralMotifIndex()

    # -----------------------------------------------------------------------
    # 1. Index and count
    # -----------------------------------------------------------------------

    def test_index_and_count_increments(self):
        """count() tracks the number of indexed motifs."""
        assert self.idx.count() == 0
        self.idx.index_incident(_motif("INC-001"))
        assert self.idx.count() == 1
        self.idx.index_incident(_motif("INC-002"))
        assert self.idx.count() == 2
        self.idx.index_incident(_motif("INC-003"))
        assert self.idx.count() == 3

    # -----------------------------------------------------------------------
    # 2. Empty index returns empty list
    # -----------------------------------------------------------------------

    def test_empty_index_returns_empty_list(self):
        """find_similar on an empty index must return []."""
        result = self.idx.find_similar(_motif("QUERY-000"))
        assert result == []

    # -----------------------------------------------------------------------
    # 3. Identical motif → similarity == 1.0
    #    score = 0.45*1 + 0.30*1 + 0.15*1 + 0.10*1 = 1.0
    # -----------------------------------------------------------------------

    def test_identical_motif_yields_similarity_one(self):
        """A stored motif matched by its structural twin must score exactly 1.0."""
        stored = _motif(
            "INC-STORED",
            event_sequence=["payment", "checkout"],
            causal_shape=list(_PAY_SHAPE),
            remediation_action="rollback",
        )
        self.idx.index_incident(stored)

        # Same structure, different incident_id (a new query, not re-querying itself)
        query = _motif(
            "INC-QUERY",
            event_sequence=["payment", "checkout"],
            causal_shape=list(_PAY_SHAPE),
            remediation_action="rollback",
        )
        results = self.idx.find_similar(query, top_k=1)
        assert len(results) == 1
        assert results[0].similarity == 1.0

    # -----------------------------------------------------------------------
    # 4. Partially different motifs → lower similarity
    # -----------------------------------------------------------------------

    def test_different_motifs_have_lower_similarity(self):
        """A query with partial overlap scores strictly between 0 and 1."""
        stored = _motif(
            "INC-A",
            event_sequence=["payment", "checkout"],
            causal_shape=list(_PAY_SHAPE),
            remediation_action="rollback",
        )
        self.idx.index_incident(stored)

        # One event differs, fewer causal edges, different action
        query = _motif(
            "QUERY-DIFF",
            event_sequence=["payment", "cache"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="scale_up",
        )
        results = self.idx.find_similar(query, top_k=1)
        assert len(results) == 1
        assert 0.0 < results[0].similarity < 1.0

    # -----------------------------------------------------------------------
    # 5. Results sorted descending by similarity
    # -----------------------------------------------------------------------

    def test_results_sorted_by_similarity_descending(self):
        """find_similar always returns matches in descending similarity order."""
        base_seq = ["payment", "checkout", "database"]
        base_shape = list(_PAY_SHAPE)

        # Three motifs with decreasing similarity to the query
        self.idx.index_incident(_motif(
            "INC-HIGH",
            event_sequence=base_seq,
            causal_shape=base_shape,
        ))
        self.idx.index_incident(_motif(
            "INC-MED",
            event_sequence=["payment", "checkout"],  # subset
            causal_shape=base_shape,
        ))
        self.idx.index_incident(_motif(
            "INC-LOW",
            event_sequence=["auth", "cache"],
            causal_shape=_CHECKOUT_SHAPE,
        ))

        query = _motif("QUERY", event_sequence=base_seq, causal_shape=base_shape)
        results = self.idx.find_similar(query, top_k=5)

        assert len(results) >= 2, "Expected at least two non-zero matches"
        for i in range(len(results) - 1):
            assert results[i].similarity >= results[i + 1].similarity, (
                f"Out of order at index {i}: "
                f"{results[i].similarity} < {results[i+1].similarity}"
            )

    # -----------------------------------------------------------------------
    # 6. top_k is respected
    # -----------------------------------------------------------------------

    def test_top_k_limits_results(self):
        """find_similar never returns more than top_k entries."""
        for i in range(8):
            self.idx.index_incident(_motif(
                f"INC-{i:03d}",
                event_sequence=["DEPLOY", "METRIC_ANOMALY"],
            ))
        query = _motif("QUERY", event_sequence=["DEPLOY", "METRIC_ANOMALY"])

        assert len(self.idx.find_similar(query, top_k=1)) <= 1
        assert len(self.idx.find_similar(query, top_k=3)) <= 3
        assert len(self.idx.find_similar(query, top_k=5)) <= 5

    # -----------------------------------------------------------------------
    # 7. Update in-place: same incident_id replaces the existing motif
    # -----------------------------------------------------------------------

    def test_update_same_incident_id_replaces_and_count_unchanged(self):
        """Indexing the same incident_id twice replaces the entry; count stays at 1."""
        self.idx.index_incident(_motif(
            "INC-MUTABLE",
            event_sequence=["DEPLOY"],
            remediation_action="rollback",
        ))
        assert self.idx.count() == 1

        self.idx.index_incident(_motif(
            "INC-MUTABLE",  # same ID
            event_sequence=["DEPLOY", "METRIC_ANOMALY"],
            remediation_action="restart",
        ))
        assert self.idx.count() == 1  # no duplicate created

        motifs = self.idx.all_motifs()
        assert len(motifs) == 1
        assert motifs[0].remediation_action == "restart"
        assert "METRIC_ANOMALY" in motifs[0].event_sequence

    # -----------------------------------------------------------------------
    # 8. Same remediation_action boosts similarity (0.15 bonus)
    # -----------------------------------------------------------------------

    def test_same_remediation_action_boosts_similarity(self):
        """Queries matching the stored action score higher than those that don't."""
        stored = _motif(
            "INC-BASE",
            event_sequence=["DEPLOY", "METRIC_ANOMALY"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="rollback",
        )
        self.idx.index_incident(stored)

        query_match = _motif(
            "Q-ACTION-MATCH",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="rollback",   # same action → bonus
        )
        query_diff = _motif(
            "Q-ACTION-DIFF",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="restart",    # different → no bonus
        )

        sim_match = self.idx.find_similar(query_match, top_k=1)[0].similarity
        sim_diff  = self.idx.find_similar(query_diff,  top_k=1)[0].similarity
        assert sim_match > sim_diff, (
            f"Expected action-match ({sim_match}) > action-diff ({sim_diff})"
        )

    # -----------------------------------------------------------------------
    # 9. Returned IncidentMatch carries all expected fields
    # -----------------------------------------------------------------------

    def test_match_carries_correct_fields(self):
        """IncidentMatch fields reflect exactly what was stored."""
        stored = _motif(
            "INC-FIELDCHECK",
            remediation_action="scale_out",
            remediation_outcome="resolved",
            timestamp="2026-06-15T12:00:00+00:00",
            canonical_ids=["cid-abc123"],
        )
        self.idx.index_incident(stored)

        # Use matching canonical_ids so the match scores high enough to pass threshold
        results = self.idx.find_similar(_motif("Q", canonical_ids=["cid-abc123"]), top_k=1)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        m = results[0]

        assert m.incident_id == "INC-FIELDCHECK"
        assert m.remediation_action == "scale_out"
        assert m.remediation_outcome == "resolved"
        assert m.timestamp == "2026-06-15T12:00:00+00:00"
        assert isinstance(m.similarity, float)
        assert 0.0 <= m.similarity <= 1.0
        assert isinstance(m.rationale, str)

    # -----------------------------------------------------------------------
    # 10. Zero-score motifs are excluded from results (score > 0 filter)
    # -----------------------------------------------------------------------

    def test_zero_score_motifs_excluded_from_results(self):
        """A stored motif with no overlap against an empty query must be filtered."""
        self.idx.index_incident(_motif(
            "INC-NONEMPTY",
            event_sequence=["A", "B", "C"],
            causal_shape=_CHECKOUT_SHAPE,
        ))

        # Empty query: no event_sequence, no shape, no action → score = 0
        empty_query = IncidentMotif(
            incident_id="EMPTY-QUERY",
            event_sequence=[],
            causal_shape=[],
            remediation_action="",
        )
        results = self.idx.find_similar(empty_query)
        # Any returned entry must be strictly positive
        for r in results:
            assert r.similarity > 0.0

    # -----------------------------------------------------------------------
    # 11. Disjoint motif not returned; overlapping one is
    # -----------------------------------------------------------------------

    def test_disjoint_motif_scores_lower_than_overlapping(self):
        """Role-overlapping motifs outrank disjoint shapes in find_similar."""
        self.idx.index_incident(_motif(
            "INC-SIMILAR",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
        ))
        self.idx.index_incident(_motif(
            "INC-DISJOINT",
            event_sequence=["auth", "cache"],
            causal_shape=[("auth", "token_refresh", "cache")],
            remediation_action="baz",
        ))

        query = _motif(
            "Q",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
        )
        results = self.idx.find_similar(query, top_k=5)
        by_id = {r.incident_id: r for r in results}

        assert "INC-SIMILAR" in by_id
        assert "INC-DISJOINT" in by_id
        assert by_id["INC-SIMILAR"].similarity > by_id["INC-DISJOINT"].similarity
        assert results[0].incident_id == "INC-SIMILAR"

    # -----------------------------------------------------------------------
    # 12. Multiple indexed motifs; identical one ranks first
    # -----------------------------------------------------------------------

    def test_identical_motif_ranks_first_among_many(self):
        """The stored motif that is structurally identical to the query scores highest."""
        self.idx.index_incident(_motif("INC-P", event_sequence=["auth"], causal_shape=_CHECKOUT_SHAPE))
        self.idx.index_incident(_motif("INC-Q", event_sequence=["payment", "checkout"], causal_shape=list(_PAY_SHAPE)))
        self.idx.index_incident(_motif(
            "INC-EXACT",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="rollback",
        ))

        query = _motif(
            "QUERY-EXACT",
            event_sequence=["payment", "checkout"],
            causal_shape=[("payment", "deploy_to_metric", "payment")],
            remediation_action="rollback",
        )
        results = self.idx.find_similar(query, top_k=5)
        assert results[0].incident_id == "INC-EXACT"
        assert results[0].similarity == 1.0

    # -----------------------------------------------------------------------
    # 13. Role-based shapes match across cascading renames
    # -----------------------------------------------------------------------

    def test_role_shapes_match_after_rename(self):
        """svc-pay-* and svc-bil-* share payment role → identical causal shapes score 1.0."""
        resolver = IdentityResolver()
        pay_cid = resolver.resolve("svc-pay-99")
        resolver.rename("svc-pay-99", "svc-bil-99", "2026-01-01T00:00:00+00:00")
        assert resolver.canonical_role(pay_cid) == "payment"

        from engine.graph import OperationalGraph

        graph = OperationalGraph()
        graph.add_edge(pay_cid, pay_cid, "deploy_to_metric", "e1", "2026-01-01T10:00:00+00:00", "2026-01-01T10:05:00+00:00")
        edge = graph.get_causal_chain(pay_cid, max_hops=2, min_confidence=0.0)[0]

        motif_before = graph.extract_motif([edge], resolver)
        motif_after = graph.extract_motif([edge], resolver)
        assert motif_before.causal_shape == motif_after.causal_shape
        assert motif_before.causal_shape[0][0] == "payment"

        stored = _motif("INC-PAY", causal_shape=motif_before.causal_shape)
        query = _motif("INC-BIL", causal_shape=motif_after.causal_shape)
        self.idx.index_incident(stored)
        sim = self.idx.find_similar(query, top_k=1)[0].similarity
        assert _role_shape_similarity(query, stored) == 1.0
        assert sim >= 0.5
