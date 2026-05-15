# Part 2: Deterministic Weight Sweep & Retrieval Policy Redesign

## Overview

This document provides integration guidance for the 5 new modules that implement Part 2 of the diagnostic framework:

1. **weight_sweep_framework.py** — Deterministic hyperparameter sweep
2. **two_stage_retrieval.py** — Two-stage incident retrieval policy
3. **remediation_optimizer.py** — Bayesian remediation confidence scoring
4. **family_representation.py** — Incident family diversification
5. **decoy_suppression.py** — Aggressive decoy detection and suppression

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│ bench_run.py (Entry Point)                                      │
│  • Runs benchmark with given config                             │
│  • Measures: recall@5, precision@5, remediation_acc, latency    │
└────┬────────────────────────────────────────────────────────────┘
     │
     ├──────────────────┬──────────────────────┬──────────────────┐
     │                  │                      │                  │
     v                  v                      v                  v
┌─────────────┐  ┌──────────────────────┐  ┌──────────────┐  ┌──────────────┐
│   Engine    │  │  Weight Sweep        │  │ Two-Stage    │  │ Decoy        │
│ (adapters/) │  │ Optimizer            │  │ Retrieval    │  │ Suppression  │
└─────────────┘  └──────────────────────┘  └──────────────┘  └──────────────┘
     │                  │                         │                  │
     │ reconstruct      │ run_sweep()             │ select_top_k()   │ is_likely_decoy()
     │ context()        │ evaluate_config()       │ is_family_safe() │ suppress_confidence()
     │                  │ parameter_importance()  │ compute_evidence │ build_suppression_policy()
     │                  │                         │                  │
     ├─────────────┬────┴────────────────────────┴──────────────────┴────┐
     │             │                                                      │
     v             v                                                      v
┌─────────────────────────────────────┐  ┌──────────────────────────────┐
│ Reranking Pipeline in Adapter       │  │ Remediation Optimizer        │
│ (adapters/engine.py, line 225+)     │  │ (remediation_optimizer.py)   │
│                                     │  │                              │
│ 1. Canonical service boost/penalty  │  │ • score_remediation_family() │
│ 2. Graph distance penalty           │  │ • rank_remediations()        │
│ 3. Evidence agreement bonus         │  │ • confidence_for_action()    │
│ 4. Family diversification           │  │                              │
│    (family_representation.py)       │  │ Returns top-3 actions with   │
│ 5. Stage-A high-precision filter    │  │ Bayesian confidence scores   │
│ 6. Decoy suppression                │  │                              │
│    (decoy_suppression.py)           │  └──────────────────────────────┘
└─────────────────────────────────────┘
             │
             v
        Output: Top-5 similar incidents + Top-3 remediations
```

## Module Details & Integration Points

### 1. weight_sweep_framework.py

**Purpose**: Deterministic hyperparameter optimization for the engine configuration.

**Key Classes**:
- `WeightSweepOptimizer` — Main optimizer
- `SweepResult` — Individual configuration result

**Main Methods**:

```python
optimizer = WeightSweepOptimizer()

# Generate parameter grid
configs = optimizer.generate_sweep_configs(
    parameter_ranges={
        "evidence_boost": [0.05, 0.10, 0.15, 0.20],
        "same_cid_boost": [0.20, 0.30, 0.40],
        "cross_cid_penalty": [0.10, 0.15, 0.20],
        "stageA_min_similarity": [0.50, 0.60, 0.70],
    },
    max_configs=30,  # Random sampling if >5000 combos
)

# Evaluate one config
score_dict = optimizer.evaluate_config(
    config={"evidence_boost": 0.15, "same_cid_boost": 0.32, ...},
    seed_list=[9999, 31415, 27182],
    bench_run_fn=create_bench_runner(),  # See integration section below
    mode="fast",
)

# Run full sweep
results = optimizer.run_sweep(
    parameter_ranges=param_ranges,
    seed_list=[9999, 31415, 27182],
    bench_run_fn=create_bench_runner(),
    mode="fast",
    max_configs=50,
)

# Access results
print(f"Best config: {results['best_config']}")
print(f"Best score: {results['best_score']:.4f}")
print(f"Parameter importance: {results['parameter_importance']}")

# Save for reproducibility
optimizer.save_results("sweep_results.json")
```

**Integration with bench_run.py**:

```python
# In your script:
from weight_sweep_framework import create_bench_runner, WeightSweepOptimizer

bench_runner = create_bench_runner(root_dir="/Users/apple/SRE")
optimizer = WeightSweepOptimizer()

results = optimizer.run_sweep(
    parameter_ranges={...},
    seed_list=[9999, 31415, 27182],
    bench_run_fn=bench_runner,
)
```

**Scoring Weights**:
- Recall@5: 50% (most important)
- Precision@5: 30%
- Remediation_acc: 15%
- Latency: 5% (soft penalty, asymptotes at 2000ms)

---

### 2. two_stage_retrieval.py

**Purpose**: High-precision incident retrieval with controlled recall recovery.

**Key Classes**:
- `TwoStageRetrieval` — Main retrieval policy

**Architecture**:

```
Stage A (High Precision):
  ├─ Similarity >= 0.60 (tunable)
  ├─ is_family_safe() checks:
  │  ├─ Has incident ID (startswith INC-)
  │  ├─ Canonical ID consistency
  │  ├─ Action family consistency
  │  └─ Evidence requirements
  └─ Returns top 3-5 results

If Stage A < 3 results → Trigger Stage B:
  ├─ Similarity >= 0.50 (tunable)
  ├─ Not already in Stage A
  └─ Combined cap at 5 total
```

**Integration into adapters/engine.py**:

```python
from two_stage_retrieval import TwoStageRetrieval

class Engine:
    def __init__(self):
        # ... existing code ...
        self.retrieval_policy = TwoStageRetrieval(
            stageA_min_similarity=self._cfg.get("stageA_min_similarity", 0.60),
            stageB_min_similarity=0.50,
            min_stageA_results=3,
            max_results=5,
        )

    def reconstruct_context(self, signal, mode="fast"):
        # ... existing assembly code ...
        
        # After assembler returns context
        related_events = ctx.get("related_events", [])
        similar = ctx.get("similar_past_incidents", [])
        
        # Build evidence dict
        evidence_dict = self.retrieval_policy.build_evidence_dict(related_events)
        
        # Apply two-stage retrieval
        similar = self.retrieval_policy.select_top_k(
            similar, 
            mode="precision",
            evidence_dict=evidence_dict
        )
        
        return ctx
```

**Example Usage**:

```python
retriever = TwoStageRetrieval(
    stageA_min_similarity=0.65,
    stageB_min_similarity=0.50,
    min_stageA_results=3,
    max_results=5,
)

# Evaluate evidence
evidence_dict = retriever.build_evidence_dict(related_events)

# Get top-k results
top_k = retriever.select_top_k(
    candidates=similar_matches,
    mode="precision",
    evidence_dict=evidence_dict,
)

# Deduplicate families
dedup = retriever.deduplicate_families(top_k, keep_top_per_family=1)
```

---

### 3. remediation_optimizer.py

**Purpose**: Rank remediation actions by Bayesian-posterior confidence.

**Key Classes**:
- `RemediationOptimizer` — Main optimizer

**Strategy**:

```
For each candidate action in similar_matches:
  1. Aggregate success rate across matches with that action
  2. Weight by similarity (higher similarity = more credible)
  3. Compute Bayesian Beta posterior:
     - Prior: Beta(2, 2) ≈ uniform, centered at 0.5
     - Likelihood: observed successes/failures
     - Posterior: (alpha + successes) / (alpha + beta + total)
  4. Scale by data credibility and similarity
  5. Return top-3 with confidence
```

**Integration into adapters/engine.py**:

```python
from remediation_optimizer import RemediationOptimizer

class Engine:
    def __init__(self):
        # ... existing code ...
        self.remed_optimizer = RemediationOptimizer(
            prior_success_rate=0.5,
            min_history_count=2,
        )

    def reconstruct_context(self, signal, mode="fast"):
        # ... existing code ...
        
        similar = ctx.get("similar_past_incidents", [])
        
        # Rank remediations
        remediations = self.remed_optimizer.rank_remediations(
            similar_matches=similar,
            graph=self.graph,
            resolver=self.resolver,
        )
        
        ctx["suggested_remediations"] = remediations
        return ctx
```

**Example Usage**:

```python
optimizer = RemediationOptimizer()

matches = [
    {
        "incident_id": "INC-X-1",
        "similarity": 0.85,
        "remediation_action": "restart_service",
        "remediation_outcome": "resolved",
    },
    # ... more matches ...
]

# Rank remediations
ranked = optimizer.rank_remediations(matches)

# Results:
# [
#   {
#       "action": "restart_service",
#       "target": "svc-A",
#       "confidence": 0.75,
#       "success_count": 5,
#       "total_count": 6,
#   },
#   ...
# ]
```

**Confidence Computation**:
- Empirical success rate: successes / total
- Bayesian posterior: (alpha + successes) / (alpha + beta + total)
- Credibility: min(1.0, total / 10.0)
- Final: blend empirical (40%) + posterior (60%), scaled by credibility
- Capped in [0.05, 0.99]

---

### 4. family_representation.py

**Purpose**: Prevent same-family clustering in top-5 results.

**Key Classes**:
- `FamilyRepresentation` — Family grouping and ranking

**Family Extraction**:
- Incident ID format: "INC-X-5"
- Family ID: suffix after last hyphen → "5"
- Multiple incidents with family "5" → only keep 1 in top-5

**Integration into adapters/engine.py**:

```python
from family_representation import FamilyRepresentation

class Engine:
    def reconstruct_context(self, signal, mode="fast"):
        # ... existing reranking ...
        
        similar = ctx.get("similar_past_incidents", [])
        
        # Apply family diversification
        similar = FamilyRepresentation.diversify_by_family(
            matches=similar,
            target_count=5,
            keep_per_family=1,
        )
        
        ctx["similar_past_incidents"] = similar
        return ctx
```

**Example Usage**:

```python
from family_representation import FamilyRepresentation

candidates = [
    {"incident_id": "INC-X-5", "similarity": 0.90},  # Family 5
    {"incident_id": "INC-Y-5", "similarity": 0.85},  # Family 5
    {"incident_id": "INC-Z-5", "similarity": 0.80},  # Family 5
    {"incident_id": "INC-A-3", "similarity": 0.75},  # Family 3
    {"incident_id": "INC-B-2", "similarity": 0.70},  # Family 2
]

# Diversify: 1 per family max
diversified = FamilyRepresentation.diversify_by_family(
    candidates, target_count=5, keep_per_family=1
)
# Result: [INC-X-5, INC-A-3, INC-B-2, ...]  (only 1 from family 5)

# Rank families by strength
ranked = FamilyRepresentation.rank_families_by_strength(candidates)
# Families ranked by: mean_similarity (40%) + confidence_margin (40%) + count (20%)

# Check diversity
score = FamilyRepresentation.compute_family_diversity_score(diversified)
# 1.0 = all unique families, 0.0 = all same family
```

---

### 5. decoy_suppression.py

**Purpose**: Detect and suppress low-evidence (decoy) incident matches.

**Key Classes**:
- `DecoySuppressionEngine` — Main suppression engine
- `SuppressionPolicy` — Configuration for suppression

**Decoy Detection**:

```
Incident is "decoy" if ANY of these are missing:
  ✓ has_deploy: At least 1 deploy event
  ✓ has_metric: At least 1 metric event
  ✓ has_trace_log: At least 1 trace or log event

If incomplete → suppress matches:
  • Confidence multiplied by 0.60
  • Similarity capped at 0.45
  → Prevents decoys from dominating top-5
```

**Integration into adapters/engine.py**:

```python
from decoy_suppression import DecoySuppressionEngine

class Engine:
    def __init__(self):
        # ... existing code ...
        self.decoy_engine = DecoySuppressionEngine(
            decoy_confidence_multiplier=0.60,
            decoy_similarity_cap=0.45,
        )

    def reconstruct_context(self, signal, mode="fast"):
        # ... existing code ...
        
        related_events = ctx.get("related_events", [])
        similar = ctx.get("similar_past_incidents", [])
        remediations = ctx.get("suggested_remediations", [])
        
        # Build suppression policy
        policy = self.decoy_engine.build_suppression_policy(related_events)
        
        # Apply suppression
        similar = self.decoy_engine.apply_suppression(similar, policy)
        remediations = self.decoy_engine.apply_suppression(remediations, policy)
        
        ctx["similar_past_incidents"] = similar
        ctx["suggested_remediations"] = remediations
        
        return ctx
```

**Example Usage**:

```python
from decoy_suppression import DecoySuppressionEngine

engine = DecoySuppressionEngine(
    decoy_confidence_multiplier=0.60,
    decoy_similarity_cap=0.45,
)

# Check if decoy
is_decoy = engine.is_likely_decoy(signal, related_events)

# Build policy
policy = engine.build_suppression_policy(related_events)
# Returns: SuppressionPolicy(
#     is_decoy=True,
#     confidence_multiplier=0.60,
#     similarity_cap=0.45,
# )

# Apply suppression
matches = [{"similarity": 0.80, "confidence": 0.75}, ...]
suppressed = engine.apply_suppression(matches, policy)
# Result: {"similarity": 0.45, "confidence": 0.45}

# Analyze risk
analysis = engine.analyze_decoy_risk(signal, related_events, matches)
# Returns detailed breakdown of evidence and suppression
```

---

## Full Integration Example

Here's how all 5 modules work together in the adapter:

```python
# /Users/apple/SRE/Anvil-P-E/bench-p02-context/adapters/engine.py

from two_stage_retrieval import TwoStageRetrieval
from family_representation import FamilyRepresentation
from remediation_optimizer import RemediationOptimizer
from decoy_suppression import DecoySuppressionEngine

class Engine:
    def __init__(self):
        self.resolver = IdentityResolver()
        self.store = EventStore()
        self.graph = OperationalGraph()
        self.motifs = BehavioralMotifIndex()
        self.assembler = ContextAssembler()
        
        # NEW: Initialize Part 2 modules
        self.retrieval_policy = TwoStageRetrieval(
            stageA_min_similarity=0.60,
            stageB_min_similarity=0.50,
        )
        self.remed_optimizer = RemediationOptimizer()
        self.decoy_engine = DecoySuppressionEngine()
        
        self._cfg = {
            "same_cid_boost": 0.32,
            "cross_cid_penalty": 0.22,
            # ... other params ...
            "stageA_min_similarity": 0.52,
            "decoy_cap_similarity": 0.39,
        }

    def reconstruct_context(self, signal, mode="fast"):
        # Phase 1: Assemble base context
        ctx = self.assembler.assemble(
            signal=signal,
            mode=mode,
            resolver=self.resolver,
            event_store=self.store,
            graph=self.graph,
            motif_index=self.motifs,
        )

        related_events = ctx.get("related_events", [])
        similar = ctx.get("similar_past_incidents", [])
        remediations = ctx.get("suggested_remediations", [])

        # Phase 2: Decoy suppression
        is_decoy = self.decoy_engine.is_likely_decoy(signal, related_events)
        policy = self.decoy_engine.build_suppression_policy(related_events)
        
        similar = self.decoy_engine.apply_suppression(similar, policy)
        remediations = self.decoy_engine.apply_suppression(remediations, policy)

        # Phase 3: Two-stage retrieval for high precision
        evidence_dict = self.retrieval_policy.build_evidence_dict(related_events)
        similar = self.retrieval_policy.select_top_k(
            similar,
            mode="precision",
            evidence_dict=evidence_dict,
        )

        # Phase 4: Family diversification
        similar = FamilyRepresentation.diversify_by_family(
            similar,
            target_count=5,
            keep_per_family=1,
        )

        # Phase 5: Remediation optimization
        remediations = self.remed_optimizer.rank_remediations(
            similar,
            graph=self.graph,
            resolver=self.resolver,
        )

        ctx["similar_past_incidents"] = similar
        ctx["suggested_remediations"] = remediations

        return ctx
```

---

## Running a Weight Sweep

### Example 1: Quick Test Sweep

```python
#!/usr/bin/env python3
# sweep_test.py

from weight_sweep_framework import WeightSweepOptimizer, create_bench_runner

# Define parameter ranges
param_ranges = {
    "evidence_boost": [0.05, 0.10, 0.15, 0.20],
    "same_cid_boost": [0.20, 0.30, 0.40],
    "cross_cid_penalty": [0.10, 0.15, 0.20],
    "stageA_min_similarity": [0.50, 0.60, 0.70],
    "decoy_cap_similarity": [0.35, 0.40, 0.45],
}

optimizer = WeightSweepOptimizer()
bench_runner = create_bench_runner()

# Run sweep
results = optimizer.run_sweep(
    parameter_ranges=param_ranges,
    seed_list=[9999, 31415, 27182],
    bench_run_fn=bench_runner,
    mode="fast",
    max_configs=30,  # Random sampling to 30 configs
    timeout_s=300,
)

# Print results
print(f"Best config: {results['best_config']}")
print(f"Best score: {results['best_score']:.4f}")
print(f"Parameter importance:")
for param, importance in sorted(
    results['parameter_importance'].items(),
    key=lambda x: x[1],
    reverse=True,
):
    print(f"  {param}: {importance:.3f}")

# Save results
optimizer.save_results("sweep_results.json")
```

### Example 2: Precision-Focused Sweep

```python
# sweep_precision.py

# Narrow ranges focused on precision improvement
param_ranges = {
    "stageA_min_similarity": [0.55, 0.60, 0.65, 0.70],
    "evidence_boost": [0.10, 0.15, 0.20],
    "same_cid_boost": [0.25, 0.30, 0.35],
}

results = optimizer.run_sweep(
    parameter_ranges=param_ranges,
    seed_list=[9999, 31415, 27182, 16180],
    bench_run_fn=bench_runner,
    mode="fast",
    max_configs=50,
)
```

---

## Performance Expectations

### Per-Module Overhead

| Module | Time | Memory | Notes |
|--------|------|--------|-------|
| Two-Stage Retrieval | <5ms | <1MB | Filtering + scoring |
| Family Diversification | <3ms | <0.5MB | Grouping + sorting |
| Remediation Optimizer | <10ms | <2MB | Aggregation + Bayesian |
| Decoy Suppression | <2ms | <0.5MB | Simple checks |
| **Total per query** | ~20ms | ~4MB | Negligible latency impact |

### Weight Sweep Costs

- **Single config eval** (1 seed): ~30-60s
- **5 seeds per config**: ~2-5 minutes
- **30 configs × 5 seeds**: ~2-2.5 hours
- **Recommend**: `max_configs=30-50` for quick iteration

---

## Testing

### Unit Tests

```python
# test_part2_modules.py

import pytest
from two_stage_retrieval import TwoStageRetrieval
from family_representation import FamilyRepresentation
from remediation_optimizer import RemediationOptimizer
from decoy_suppression import DecoySuppressionEngine

def test_two_stage_retrieval():
    retriever = TwoStageRetrieval(stageA_min_similarity=0.60)
    candidates = [
        {"incident_id": "INC-X-1", "similarity": 0.85, "canonical_ids": ["svc-A"]},
        {"incident_id": "INC-Y-1", "similarity": 0.55, "canonical_ids": []},
    ]
    evidence = {"has_deploy": True, "has_metric": True, "has_trace_log": True}
    
    top_k = retriever.select_top_k(candidates, evidence_dict=evidence)
    assert len(top_k) == 1
    assert top_k[0]["incident_id"] == "INC-X-1"

def test_family_diversification():
    candidates = [
        {"incident_id": "INC-X-5", "similarity": 0.90},
        {"incident_id": "INC-Y-5", "similarity": 0.85},
        {"incident_id": "INC-A-3", "similarity": 0.80},
    ]
    
    diversified = FamilyRepresentation.diversify_by_family(
        candidates, target_count=5, keep_per_family=1
    )
    
    families = {
        FamilyRepresentation.family_id_from_incident(m["incident_id"])
        for m in diversified
    }
    assert len(families) == 2  # Only 2 families (5 and 3)
    assert len(diversified) == 2

def test_decoy_suppression():
    engine = DecoySuppressionEngine()
    matches = [{"similarity": 0.80}]
    
    # Complete evidence
    events_good = [
        {"kind": "deploy"},
        {"kind": "metric"},
        {"kind": "log"},
    ]
    policy_good = engine.build_suppression_policy(events_good)
    assert not policy_good.is_decoy
    
    # Incomplete evidence
    events_bad = [
        {"kind": "deploy"},
        {"kind": "metric"},
        # Missing logs
    ]
    policy_bad = engine.build_suppression_policy(events_bad)
    assert policy_bad.is_decoy
    
    suppressed = engine.apply_suppression(matches, policy_bad)
    assert suppressed[0]["similarity"] <= 0.45
```

---

## Tuning Guide

### For Higher Recall@5

```python
# Loosen Stage A threshold, accept more matches
param_ranges = {
    "stageA_min_similarity": [0.50, 0.55],  # Lower threshold
    "decoy_cap_similarity": [0.40, 0.45],    # Relax decoy cap
}
```

### For Higher Precision@5

```python
# Tighten thresholds, require stronger evidence
param_ranges = {
    "stageA_min_similarity": [0.65, 0.70],  # Higher threshold
    "evidence_boost": [0.15, 0.20],         # Boost evidence requirement
}
```

### For Better Remediation_acc

```python
# Improve remediation optimizer confidence
param_ranges = {
    "evidence_boost": [0.10, 0.15, 0.20],   # Favor matches with evidence
    "same_cid_boost": [0.25, 0.35],         # Favor in-service matches
}
```

---

## Monitoring & Diagnostics

### Check Decoy Suppression Activity

```python
engine = DecoySuppressionEngine()
analysis = engine.analyze_decoy_risk(signal, related_events, matches)

print(f"Decoy detected: {analysis['is_decoy']}")
print(f"Evidence score: {analysis['evidence_score']}")
print(f"Missing: {analysis['missing_evidence']}")
print(f"Top match suppressed: {analysis['top_match_original_sim']:.3f} → {analysis['top_match_suppressed_sim']:.3f}")
```

### Check Family Diversity

```python
from family_representation import FamilyRepresentation

diversity = FamilyRepresentation.compute_family_diversity_score(similar_matches)
print(f"Family diversity: {diversity:.2f}")  # 1.0 = all unique, 0.0 = all same
```

### Check Remediation Confidence

```python
optimizer = RemediationOptimizer()
ranked = optimizer.rank_remediations(similar_matches)

for rem in ranked:
    print(f"  {rem['action']}: {rem['confidence']:.3f} "
          f"({rem['success_count']}/{rem['total_count']})")
```

---

## Summary

The 5 modules implement a coherent retrieval and ranking pipeline:

1. **Weight Sweep** → Optimize hyperparameters deterministically
2. **Decoy Suppression** → Detect low-evidence false positives
3. **Two-Stage Retrieval** → High-precision filtering with recall recovery
4. **Family Diversification** → Avoid top-5 clustering
5. **Remediation Optimizer** → Rank actions by Bayesian confidence

All work together to improve:
- **Recall@5** ≥ 0.65
- **Precision@5** ≥ 0.40
- **Remediation_acc** ≥ 0.80
- **Latency** ≤ 2000ms

See individual module docstrings and examples for detailed API documentation.
