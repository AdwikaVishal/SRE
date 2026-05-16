# Service Alias Tracking for Rename-Robust Incident Similarity

## Overview

This implementation adds a service alias tracking system to the Engine class that prevents incident similarity from leaking service names during rename operations. The system ensures that incident matching remains robust even when services are renamed multiple times.

## Problem Statement

The incident similarity system was leaking service names during rename-robust retrieval. When a service is renamed (e.g., `payment-svc` → `payment-svc-v2`), the system needs to ensure that:
1. Both names resolve to the same canonical ID
2. Incident fingerprints use canonical IDs, not surface names
3. Similarity matching compares canonical identifiers, not service names

## Implementation Details

### 1. Alias Map Data Structures (in Engine.__init__)

```python
self.alias_map = {}           # current_name -> canonical_id
self.reverse_alias = {}       # canonical_id -> {all known names}
self.canonical_counter = 0    # For potential internal tracking
```

- `alias_map`: Maps service names to canonical IDs for quick lookup during fingerprinting
- `reverse_alias`: Maps canonical IDs back to all known service names (for audit/debugging)
- `canonical_counter`: Reserved for future use if needed for internal ID assignment

### 2. _merge_aliases() Method

```python
def _merge_aliases(self, old_name: str, new_name: str) -> None:
    """Track service name aliases for rename-robust incident similarity.
    
    Synchronizes with IdentityResolver's canonical IDs to ensure that
    incident fingerprints use canonical identifiers rather than surface names.
    """
```

**Purpose**: Synchronizes the alias map with the IdentityResolver when a rename event occurs.

**Key Features**:
- Called automatically when topology rename events are ingested
- Ensures both old and new names map to the same canonical ID
- Maintains reverse mapping for audit trails
- Idempotent: safe to call multiple times for the same rename

**Flow**:
1. Resolve old_name and new_name to canonical IDs via the resolver
2. After resolver.rename() has been called, both should map to the same ID
3. Update alias_map to point both names to this canonical ID
4. Update reverse_alias to track all names for this ID

### 3. _canonical_fingerprint() Method

```python
def _canonical_fingerprint(self, incident_events: list) -> tuple:
    """Replace service names with canonical IDs before fingerprinting.
    
    This ensures that incident similarity matching is robust to service renames.
    Two incidents that occur on the same logical service (under different names)
    will have identical fingerprints.
    """
```

**Purpose**: Generate deterministic fingerprints using canonical IDs instead of service names.

**Features**:
- Handles both dict and object-style events
- Resolves service names to canonical IDs via alias_map
- Falls back to resolver if alias_map doesn't have the entry
- Truncates messages to 50 chars for consistent fingerprinting
- Returns sorted tuple for deterministic comparison

**Output Format**:
Each event contributes `(canonical_id, event_kind, message_snippet)` to the fingerprint, which are then sorted and returned as a tuple.

## Integration Points

### 1. Topology Rename Event Processing

When topology rename events are ingested:
```python
if change_kind == "rename":
    old_name = event.get("from", "")
    new_name = event.get("to", "")
    if old_name and new_name:
        self.resolver.rename(old_name, new_name, ts)
        # NEW: Track alias mapping for rename-robust incident similarity
        self._merge_aliases(old_name, new_name)
```

### 2. Incident Similarity Matching

The canonical_fingerprint method can be used to compare incidents:
- Two incidents on the same service (under different names) will have identical fingerprints
- Fingerprints are deterministic and sortable
- Can be used as keys in dictionaries or sets for deduplication

## Test Coverage

14 comprehensive tests validate:

1. **Alias Map Initialization**: Starts empty, properly initialized
2. **Single Rename**: Both old and new names map to same canonical ID
3. **Reverse Mapping**: Canonical ID tracks all service names
4. **Chained Renames**: Multiple sequential renames maintain same canonical ID
5. **Rename Event Ingestion**: Topology events properly update alias map
6. **Basic Fingerprinting**: Fingerprints use canonical IDs, not names
7. **Rename Robustness**: Same incident on renamed service produces identical fingerprint
8. **Mixed Event Types**: Handles both dict and object-style events
9. **Topology Event Chains**: Multiple renames are properly chained
10. **Empty Events**: Edge case handling
11. **Events Without Service**: Graceful handling of None canonical IDs
12. **Message Truncation**: Messages are truncated to 50 chars
13. **Deterministic Ordering**: Fingerprints are sorted for consistent comparison
14. **Chained Renames**: Multiple sequential service renames maintain invariants

## Performance Characteristics

- **Space Complexity**: O(n) where n = number of unique service names seen
- **Time Complexity**: 
  - Rename operation: O(1) hash table operations
  - Fingerprinting: O(m * log m) where m = number of events (due to sorting)
  - Alias lookup: O(1) average case

## Backward Compatibility

The implementation:
- Does not modify existing public API
- Is additive (only adds new attributes and methods)
- Gracefully falls back to resolver if alias_map not populated
- Can be used selectively without affecting other subsystems

## Expected Performance Improvements

Per the task specification, with this fix in place:
- **recall@5** should improve from ~0.55 to ~0.65
- Incident similarity should be more robust to service renames
- False negatives from name mismatches should be eliminated

## Future Enhancements

1. Persist alias_map to disk for state recovery
2. Use canonical_fingerprint in motif index for faster matching
3. Add explicit API for querying alias relationships
4. Implement alias chain validation and cycle detection
