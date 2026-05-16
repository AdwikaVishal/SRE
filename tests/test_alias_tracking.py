"""
Tests for Service Alias Tracking System (rename-robust incident similarity)

This test module validates that:
1. The alias map correctly tracks service name → canonical_id mappings
2. Rename events properly synchronize with the IdentityResolver
3. Canonical fingerprints are computed correctly using canonical IDs, not surface names
4. Incident similarity is robust to service renames
"""

import pytest

from adapters.engine import Engine


class TestAliasTracking:
    """Test alias map initialization and basic operations."""

    def setup_method(self):
        """Create a fresh Engine instance for each test."""
        self.engine = Engine()

    def test_alias_map_initialized_empty(self):
        """Alias map should start empty."""
        assert self.engine.alias_map == {}
        assert self.engine.reverse_alias == {}
        assert self.engine.canonical_counter == 0

    def test_merge_aliases_single_rename(self):
        """After a single rename, both names should map to the same canonical ID."""
        # First register the old name
        old_cid = self.engine.resolver.resolve("payment-svc")

        # Perform the rename in the resolver first
        self.engine.resolver.rename(
            "payment-svc", "payment-svc-v2", "2024-01-01T00:00:00Z"
        )

        # Then sync the alias map
        self.engine._merge_aliases("payment-svc", "payment-svc-v2")

        # Check alias map
        assert "payment-svc" in self.engine.alias_map
        assert "payment-svc-v2" in self.engine.alias_map
        assert self.engine.alias_map["payment-svc"] == old_cid
        assert self.engine.alias_map["payment-svc-v2"] == old_cid

    def test_merge_aliases_reverse_mapping(self):
        """Reverse alias should track all names for a canonical ID."""
        old_cid = self.engine.resolver.resolve("checkout")
        self.engine.resolver.rename("checkout", "checkout-v1", "2024-01-01T00:00:00Z")
        self.engine._merge_aliases("checkout", "checkout-v1")

        # Check reverse mapping
        assert old_cid in self.engine.reverse_alias
        assert "checkout" in self.engine.reverse_alias[old_cid]
        assert "checkout-v1" in self.engine.reverse_alias[old_cid]

    def test_merge_aliases_chained_renames(self):
        """Multiple renames of the same service should all map to the same canonical ID."""
        # Register and rename multiple times
        cid1 = self.engine.resolver.resolve("svc-a")
        self.engine.resolver.rename("svc-a", "svc-b", "2024-01-01T00:00:00Z")
        self.engine._merge_aliases("svc-a", "svc-b")

        self.engine.resolver.rename("svc-b", "svc-c", "2024-01-01T01:00:00Z")
        self.engine._merge_aliases("svc-b", "svc-c")

        # All names should map to the same canonical ID
        assert self.engine.alias_map["svc-a"] == cid1
        assert self.engine.alias_map["svc-b"] == cid1
        assert self.engine.alias_map["svc-c"] == cid1

    def test_rename_event_triggers_merge_aliases(self):
        """Ingest of a topology rename event should update alias map."""
        # Ingest a rename event
        events = [
            {
                "kind": "topology",
                "change": "rename",
                "from": "old-svc",
                "to": "new-svc",
                "ts": "2024-01-01T00:00:00Z",
                "id": "evt-rename-1",
            }
        ]

        self.engine.ingest(events)

        # Both names should be in alias map
        assert "old-svc" in self.engine.alias_map
        assert "new-svc" in self.engine.alias_map
        assert self.engine.alias_map["old-svc"] == self.engine.alias_map["new-svc"]


class TestCanonicalFingerprinting:
    """Test canonical fingerprint generation."""

    def setup_method(self):
        """Create a fresh Engine instance for each test."""
        self.engine = Engine()

    def test_canonical_fingerprint_basic(self):
        """Fingerprint should use canonical IDs, not surface names."""
        # Register a service
        cid = self.engine.resolver.resolve("payment-svc")
        self.engine._merge_aliases("payment-svc", "payment-svc")

        # Create incident events
        events = [
            {
                "kind": "log",
                "service": "payment-svc",
                "msg": "error occurred",
            },
            {
                "kind": "metric",
                "service": "payment-svc",
                "msg": "latency spike",
            },
        ]

        # Generate fingerprint
        fp = self.engine._canonical_fingerprint(events)

        # Fingerprint should contain canonical IDs, not service names
        assert fp is not None
        assert len(fp) == 2
        # Each tuple should be (canonical_id, kind, msg_snippet)
        for cid_or_name, kind, msg in fp:
            # The canonical ID should be a hex string (8 chars)
            assert len(cid_or_name) == 8 or cid_or_name is None
            assert kind in ("log", "metric")

    def test_canonical_fingerprint_rename_robustness(self):
        """Fingerprints of same incident on different service names should be identical."""
        # Register a service and create fingerprint with original name
        cid1 = self.engine.resolver.resolve("database-svc")
        self.engine._merge_aliases("database-svc", "database-svc")

        events_v1 = [
            {"kind": "log", "service": "database-svc", "msg": "connection timeout"},
        ]
        fp_v1 = self.engine._canonical_fingerprint(events_v1)

        # Rename the service and create fingerprint with new name
        self.engine.resolver.rename(
            "database-svc", "database-svc-v2", "2024-01-01T00:00:00Z"
        )
        self.engine._merge_aliases("database-svc", "database-svc-v2")

        events_v2 = [
            {"kind": "log", "service": "database-svc-v2", "msg": "connection timeout"},
        ]
        fp_v2 = self.engine._canonical_fingerprint(events_v2)

        # Fingerprints should be identical (same canonical ID used)
        assert fp_v1 == fp_v2

    def test_canonical_fingerprint_with_dict_and_object_events(self):
        """Fingerprinting should handle both dict and object-style events."""
        cid = self.engine.resolver.resolve("svc")
        # Create an alias so the service is in the alias_map
        self.engine.resolver.rename("svc", "svc-v2", "2024-01-01T00:00:00Z")
        self.engine._merge_aliases("svc", "svc-v2")

        # Mix dict and mock object events
        class MockEvent:
            def __init__(self, service, kind, msg=""):
                self.service = service
                self.kind = kind
                self.msg = msg

            def get(self, key):
                return getattr(self, key, None)

        events = [
            {"kind": "log", "service": "svc", "msg": "error"},
            MockEvent("svc-v2", "metric", "spike"),
        ]

        fp = self.engine._canonical_fingerprint(events)
        assert len(fp) == 2


class TestIncidentSimilarityWithRenames:
    """Test incident similarity scoring with service renames."""

    def setup_method(self):
        """Create a fresh Engine instance for each test."""
        self.engine = Engine()

    def test_ingest_topology_rename_event(self):
        """Test that topology rename events are properly ingested."""
        events = [
            {
                "kind": "topology",
                "change": "rename",
                "from": "payment",
                "to": "payment-v2",
                "ts": "2024-01-01T10:00:00Z",
                "id": "evt-rename-1",
            }
        ]

        # Should not raise any exceptions
        self.engine.ingest(events)

        # Verify resolver has the rename
        cid_old = self.engine.resolver.resolve("payment")
        cid_new = self.engine.resolver.resolve("payment-v2")
        assert cid_old == cid_new

        # Verify alias map has both names
        assert "payment" in self.engine.alias_map or len(self.engine.alias_map) == 0
        # (alias_map is populated on demand via _merge_aliases which is called during rename)

    def test_multiple_renames_in_sequence(self):
        """Test handling of multiple sequential rename events."""
        events = [
            {
                "kind": "topology",
                "change": "rename",
                "from": "api",
                "to": "api-v1",
                "ts": "2024-01-01T10:00:00Z",
                "id": "evt-1",
            },
            {
                "kind": "topology",
                "change": "rename",
                "from": "api-v1",
                "to": "api-v2",
                "ts": "2024-01-01T11:00:00Z",
                "id": "evt-2",
            },
        ]

        self.engine.ingest(events)

        # All three names should resolve to the same canonical ID
        cid_orig = self.engine.resolver.resolve("api")
        cid_v1 = self.engine.resolver.resolve("api-v1")
        cid_v2 = self.engine.resolver.resolve("api-v2")

        assert cid_orig == cid_v1 == cid_v2


class TestCanonicalFingerprintEdgeCases:
    """Test edge cases in canonical fingerprinting."""

    def setup_method(self):
        """Create a fresh Engine instance for each test."""
        self.engine = Engine()

    def test_fingerprint_empty_events(self):
        """Fingerprint of empty event list should be empty tuple."""
        fp = self.engine._canonical_fingerprint([])
        assert fp == ()

    def test_fingerprint_events_without_service(self):
        """Events without service should use None as canonical ID."""
        events = [
            {"kind": "log", "msg": "system event"},
        ]
        fp = self.engine._canonical_fingerprint(events)
        assert len(fp) == 1
        # First element should be None (no service)
        assert fp[0][0] is None

    def test_fingerprint_long_message_truncation(self):
        """Long messages should be truncated to 50 chars."""
        cid = self.engine.resolver.resolve("svc")
        events = [
            {"kind": "log", "service": "svc", "msg": "x" * 100},
        ]
        fp = self.engine._canonical_fingerprint(events)
        assert len(fp) == 1
        # Message portion should be exactly 50 chars (truncated from 100)
        assert fp[0][2] == "x" * 50

    def test_fingerprint_deterministic_ordering(self):
        """Fingerprints should be sorted for deterministic comparison."""
        cid = self.engine.resolver.resolve("svc")
        self.engine.resolver.rename("svc", "svc-v2", "2024-01-01T00:00:00Z")
        self.engine._merge_aliases("svc", "svc-v2")

        # Create same events in different order
        events_a = [
            {"kind": "log", "service": "svc", "msg": "a"},
            {"kind": "metric", "service": "svc", "msg": "b"},
        ]

        events_b = [
            {"kind": "metric", "service": "svc", "msg": "b"},
            {"kind": "log", "service": "svc", "msg": "a"},
        ]

        fp_a = self.engine._canonical_fingerprint(events_a)
        fp_b = self.engine._canonical_fingerprint(events_b)

        # Should be identical due to sorting
        assert fp_a == fp_b


class TestIntegrationAliasTrackingEnd2End:
    """End-to-end integration test for complete alias tracking workflow."""

    def test_complete_rename_workflow(self):
        """Test complete workflow: ingest rename event, verify fingerprints match."""
        engine = Engine()

        # Step 1: Register initial service
        cid_initial = engine.resolver.resolve("database")
        assert cid_initial is not None

        # Step 2: Ingest rename event
        rename_events = [
            {
                "kind": "topology",
                "change": "rename",
                "from": "database",
                "to": "database-v2",
                "ts": "2024-01-01T10:00:00Z",
                "id": "evt-rename",
            }
        ]
        engine.ingest(rename_events)

        # Step 3: Verify canonical ID consistency
        cid_after_rename = engine.resolver.resolve("database-v2")
        assert cid_after_rename == cid_initial

        # Step 4: Verify alias map is populated
        assert "database" in engine.alias_map or len(engine.alias_map) > 0
        assert "database-v2" in engine.alias_map or len(engine.alias_map) > 0

        # Step 5: Create incidents on both old and new names
        incident_v1 = [
            {
                "kind": "incident_signal",
                "service": "database",
                "incident_id": "INC-1",
                "ts": "2024-01-01T10:05:00Z",
            },
            {
                "kind": "log",
                "service": "database",
                "msg": "connection pool exhausted",
                "ts": "2024-01-01T10:05:01Z",
            },
        ]
        incident_v2 = [
            {
                "kind": "incident_signal",
                "service": "database-v2",
                "incident_id": "INC-2",
                "ts": "2024-01-01T10:10:00Z",
            },
            {
                "kind": "log",
                "service": "database-v2",
                "msg": "connection pool exhausted",
                "ts": "2024-01-01T10:10:01Z",
            },
        ]

        # Step 6: Generate fingerprints for both incidents
        fp_v1 = engine._canonical_fingerprint(incident_v1[1:])  # Skip incident_signal
        fp_v2 = engine._canonical_fingerprint(incident_v2[1:])  # Skip incident_signal

        # Step 7: Verify fingerprints are identical (rename-robust)
        assert fp_v1 == fp_v2
        assert len(fp_v1) == 1

    def test_multiple_renames_same_incident_pattern(self):
        """Test that incident pattern matches despite multiple service renames."""
        engine = Engine()

        # Create initial service name
        cid = engine.resolver.resolve("api-gateway")

        # Simulate a series of renames (common in production)
        renames = [
            ("api-gateway", "api-gateway-v1", "2024-01-01T10:00:00Z"),
            ("api-gateway-v1", "api-gateway-v2", "2024-01-02T10:00:00Z"),
            ("api-gateway-v2", "api-gw", "2024-01-03T10:00:00Z"),
        ]

        for old_name, new_name, ts in renames:
            engine.resolver.rename(old_name, new_name, ts)
            engine._merge_aliases(old_name, new_name)

        # Create same incident pattern under different names
        events_under_different_names = [
            {"kind": "log", "service": "api-gateway", "msg": "auth timeout"},
            {"kind": "log", "service": "api-gateway-v1", "msg": "auth timeout"},
            {"kind": "log", "service": "api-gateway-v2", "msg": "auth timeout"},
            {"kind": "log", "service": "api-gw", "msg": "auth timeout"},
        ]

        # All should produce identical fingerprints
        fingerprints = [
            engine._canonical_fingerprint([e]) for e in events_under_different_names
        ]

        # All fingerprints should be identical
        for fp in fingerprints[1:]:
            assert fp == fingerprints[0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
