# Precision Improvements — Removal of Diversity Front

## Summary of Changes

To fix precision while maintaining recall, the following changes were made:

### 1. **engine/assembler.py** — `_family_dedup_and_boost()`
- **Removed**: Diversity front logic that ensured one match per incident family
- **Replaced with**: Pure dedup-by-incident-id + strict similarity-score ranking
- **Effect**: Only the highest-scoring matches appear in top-5, eliminating wrong-family matches that were forced in by diversity bucketing

**Lines changed**: 245-265 (was 246-290)
- Old: `diverse_front` + `overflow` bucketing by family number
- New: Single sorted list by similarity descending

### 2. **engine/assembler.py** — `assemble()` method
- **Changed**: Match retrieval threshold from `min_similarity=0.0` to `min_similarity=0.35`
- **Effect**: High-confidence matches aren't filtered, but low-confidence noise is pre-filtered
- **Line**: ~161 (was ~160)

### 3. **engine/motifs.py** — `_compute_similarity()` function
- **Updated weights** to emphasize canonical_id overlap (the primary family discriminator):
  - `cid_sim`: 0.50 → **0.70** (canonical ID overlap dominates)
  - `shape_sim`: 0.20 → **0.10** (causal shape is secondary)
  - `seq_sim`: 0.15 → **0.10** (event sequence is secondary)
  - `action_match`: 0.10 → **0.05** (remediation action is tertiary)
  - `order_bonus`: 0.05 → **0.05** (unchanged)

- **Updated content token tiebreaker**:
  - Old: Applied only when 0.35 ≤ score ≤ 0.75 (ambiguous range)
  - New: Applies whenever content tokens are present
  - Weight: 0.80 * score + 0.20 * content_sim → **0.85 * score + 0.15 * content_sim**
  - **Rationale**: Content fingerprints provide signal at all similarity levels, weighted more conservatively

**Lines changed**: 354-432 (documentation and scoring)

### 4. **adapters/engine.py** — `_on_remediation()` method
- **Already implemented** (no changes needed):
  - Neighbor collection: cid + all 1-hop graph neighbors added to canonical_ids
  - Content token extraction: words from logs, metric names, deploy versions extracted and stored
  - **Effect**: Stored motifs are enriched; similarity scores are higher for correct-family matches

### 5. **engine/assembler.py** — `assemble()` method
- **Already implemented** (no changes needed):
  - Same neighbor-collection code as stored motifs
  - Same content token extraction from related events window
  - **Effect**: Query and stored motifs are symmetric; both include neighbors and content

## Expected Outcome

| Metric | Before | After | Target |
|--------|--------|-------|--------|
| `recall@5` | 1.0 ✅ | ≥ 0.8 | ≥ 0.8 |
| `precision@5_mean` | 0.2 ❌ | ≥ 0.6 | ≥ 0.65 |
| `remediation_acc` | 1.0 ✅ | 1.0 ✅ | 1.0 ✅ |

## Why This Works

1. **Diversity front was the culprit**: Forcing one match per family into top-5 meant wrong-family matches appeared just to fill diversity buckets.
2. **Pure similarity ranking is correct**: Only the highest-scoring matches (which are from the correct family due to dominant cid_sim weight) will appear in top-5.
3. **Stronger cid_sim weight (0.70)**: Canonical_id overlap is the primary family discriminator. Increasing its weight ensures correct-family incidents score higher.
4. **Content tokens as tiebreaker**: When structural similarity is ambiguous, content fingerprints (log messages, metric names, deploy versions) disambiguate.
5. **Neighbor collection**: Including 1-hop graph neighbors in canonical_ids makes stored and query motifs richer, improving similarity scores for truly related incidents.

## Files Modified
1. `engine/assembler.py`
2. `engine/motifs.py`
3. `adapters/engine.py` (already had the changes)
