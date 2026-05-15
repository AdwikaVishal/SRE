# Complete Changes Reference

## File 1: engine/assembler.py

### Change 1: `_family_dedup_and_boost()` function (Lines 245-265)

**Before (diversity front logic):**
```python
def _family_dedup_and_boost(
    matches: list[IncidentMatch],
) -> list[IncidentMatch]:
    """Deduplicate by incident_id, keep highest similarity.

    Then ensures family diversity: one representative per incident-family
    (the trailing number in the incident_id, e.g. '3' in 'INC-44435-3') is
    surfaced first.  With five families and k=5 this guarantees recall@5 = 1.0
    regardless of how the benchmark's ground-truth list is ordered relative to
    the eval-signal list.
    """
    if not matches:
        return matches

    # Step 1: dedup by incident_id, keeping best similarity per id
    seen_ids: dict[str, IncidentMatch] = {}
    for match in matches:
        iid = match.incident_id
        if iid not in seen_ids or match.similarity > seen_ids[iid].similarity:
            seen_ids[iid] = match
    deduplicated = sorted(seen_ids.values(), key=lambda m: m.similarity, reverse=True)

    # Step 2: diversity front — one best representative per family bucket.
    # The family bucket is the integer suffix of the incident_id.
    def _fam(m: IncidentMatch) -> int | None:
        try:
            return int(m.incident_id.rsplit("-", 1)[-1])
        except (ValueError, IndexError):
            return None

    seen_fam: set = set()
    diverse_front: list[IncidentMatch] = []
    overflow: list[IncidentMatch] = []
    for m in deduplicated:
        fid = _fam(m)
        if fid not in seen_fam:
            seen_fam.add(fid)
            diverse_front.append(m)
        else:
            overflow.append(m)

    # Return: diversity representatives first (sorted by similarity within
    # the front), then remaining matches for padding / precision.
    diverse_front.sort(key=lambda m: m.similarity, reverse=True)
    return diverse_front + overflow
```

**After (pure dedup + strict similarity ranking):**
```python
def _family_dedup_and_boost(
    matches: list[IncidentMatch],
) -> list[IncidentMatch]:
    """Deduplicate by incident_id, keep highest similarity.

    Pure dedup with strict similarity-score ranking — no diversity front,
    no family bucketing. Only the highest-scoring matches appear in the top-5.
    """
    if not matches:
        return matches

    # Dedup by incident_id, keeping best similarity per id
    seen_ids: dict[str, IncidentMatch] = {}
    for match in matches:
        iid = match.incident_id
        if iid not in seen_ids or match.similarity > seen_ids[iid].similarity:
            seen_ids[iid] = match

    # Return deduplicated matches sorted strictly by similarity descending
    deduplicated = sorted(seen_ids.values(), key=lambda m: m.similarity, reverse=True)
    return deduplicated
```

### Change 2: `assemble()` method — Match retrieval threshold (Line ~161)

**Before:**
```python
# CRITICAL: Use high top_k and min_similarity=0.0 to get all candidates
matches = motif_index.find_similar(current_motif, top_k=50, min_similarity=0.0)
matches = _family_dedup_and_boost(matches)
# No post-retrieval filter – keep everything for max recall
```

**After:**
```python
# Retrieve matches with low threshold to maximize precision through similarity sorting
matches = motif_index.find_similar(current_motif, top_k=50, min_similarity=0.35)
matches = _family_dedup_and_boost(matches)
```

---

## File 2: engine/motifs.py

### Change: `_compute_similarity()` function (Lines 348-432)

**Modified docstring** (Lines 354-360):
- **Before weights**: 0.50 cid, 0.20 shape, 0.15 seq, 0.10 action, 0.05 order
- **After weights**: 0.70 cid, 0.10 shape, 0.10 seq, 0.05 action, 0.05 order

**Before (old formula, lines 378-386):**
```python
# NEW FORMULA with canonical_id as primary signal
score = (
    0.50 * cid_sim
    + 0.20 * shape_sim
    + 0.15 * seq_sim
    + 0.10 * action_match
    + 0.05 * order_bonus
)

# Content fingerprint tie-breaker (only for ambiguous structural similarity)
content_sim_used = False
content_sim = 0.0
if 0.35 <= score <= 0.75 and query.content_tokens and stored.content_tokens:
    content_sim = _jaccard(set(query.content_tokens), set(stored.content_tokens))
    score = 0.80 * score + 0.20 * content_sim
    content_sim_used = True
```

**After (new formula with dominant cid_sim, lines 378-397):**
```python
# UPDATED FORMULA with canonical_id dominance (0.70)
score = (
    0.70 * cid_sim
    + 0.10 * shape_sim
    + 0.10 * seq_sim
    + 0.05 * action_match
    + 0.05 * order_bonus
)

# Content token tiebreaker: apply to all scores with content tokens present
content_sim_used = False
content_sim = 0.0
if query.content_tokens and stored.content_tokens:
    content_sim = _jaccard(set(query.content_tokens), set(stored.content_tokens))
    score = 0.85 * score + 0.15 * content_sim
    content_sim_used = True
```

**Rationale in comment updates:**
- "only for ambiguous structural similarity" → removed (applies always when tokens present)
- Weight ratio updated from 0.80/0.20 to 0.85/0.15 (content signal less dominant)

---

## File 3: adapters/engine.py

### Status: No changes needed

The following features are **already implemented** in the current version:

1. **`_on_remediation()` method (Lines 401-471)**:
   - ✅ Neighbor collection: cid + all 1-hop graph neighbors added to canonical_ids
   - ✅ Content token extraction from remediation window events
   - ✅ Tokens from log messages, metric names, deploy versions

2. **`assemble()` method in engine/assembler.py (Lines 130-162)**:
   - ✅ Same neighbor-collection code
   - ✅ Same content token extraction from related events
   - ✅ Query and stored motifs are symmetric

These were implemented correctly and require no modifications.

---

## Summary Table

| File | Function | Change | Lines |
|------|----------|--------|-------|
| assembler.py | `_family_dedup_and_boost()` | Remove diversity front; pure similarity ranking | 245-265 |
| assembler.py | `assemble()` | Change `min_similarity` from 0.0 to 0.35 | ~161 |
| motifs.py | `_compute_similarity()` | Update weights: cid 0.70, shape 0.10, seq 0.10, action 0.05; content weight 0.85/0.15 | 354-432 |
| engine.py | `_on_remediation()` | Already implemented ✅ | 401-471 |
| assembler.py | `assemble()` | Already implemented ✅ | 130-162 |

---

## Verification Commands

All files compile successfully:

```bash
python3 -m py_compile engine/assembler.py engine/motifs.py adapters/engine.py
```

Run benchmark:

```bash
cd "/Users/apple/Downloads/Mini_Anvil copy/Anvil-P-E/bench-p02-context"
export PYTHONPATH="$PWD/../.."
python run.py --adapter adapters.engine:Engine --mode fast \
  --seeds 42 101 --n-services 30 --days 14 \
  --out ../../report.json
cat ../../report.json | grep -A 6 aggregated
```

Expected scores:
- `recall@5` ≥ 0.8 (was 1.0)
- `precision@5_mean` ≥ 0.65 (was 0.2)
- `remediation_acc` = 1.0 (maintained)
