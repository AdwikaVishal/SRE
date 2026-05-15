# Diagnostic and Analysis Framework

## Overview

This framework provides comprehensive per-incident diagnostics and failure analysis for the P-02 benchmark. It enables systematic identification of failure modes and their root causes.

## Components

### 1. `diagnostic_extractor.py` — Incident-level Diagnostic Extraction

**Class:** `DiagnosticExtractor`

Extracts structured, deterministic diagnostic data for each incident.

**Key Method:**
```python
DiagnosticExtractor.dump_incident_diagnostics(
    seed: int,
    incident_idx: int,
    ground_truth: dict,
    prediction: dict,
    related_events: list,
    similar_matches: list,
    remediations: list,
    latency_ms: float
) -> dict
```

**Output Structure:**
```json
{
  "meta": {
    "seed": 42,
    "incident_idx": 0,
    "incident_id": "incident-xyz-5",
    "query_latency_ms": 42.3
  },
  "ground_truth": {
    "family_id": 5,
    "is_decoy": false,
    "canonical_service_id": "payment-service",
    "expected_remediation": "restart_worker"
  },
  "prediction": {
    "top_5_families": [
      {"rank": 1, "incident_id": "...", "family": 5, "similarity": 0.87, "rationale": "..."},
      {"rank": 2, "incident_id": "...", "family": 3, "similarity": 0.62, ...},
      ...
    ],
    "top_remediation_action": {
      "action": "restart_worker",
      "target": "worker-pool",
      "confidence": 0.78,
      "historical_outcome": "success"
    },
    "num_confident_matches": 2
  },
  "graph_evidence": {
    "has_deploy": true,
    "has_metric": true,
    "has_trace_or_log": true,
    "has_remediation": true,
    "total_events": 23,
    "event_kinds_breakdown": {"deploy": 2, "metric": 8, ...}
  },
  "scoring_attribution": {
    "top_candidate_family": 5,
    "top_candidate_similarity": 0.87,
    "match_outcome": "correct_rank1"
  }
}
```

---

### 2. `benchmark_diagnostics.py` — Harness Integration & Collection

**Class:** `BenchmarkDiagnosticsCollector`

Integrates with `bench_run.py` to collect diagnostics during evaluation.

**Key Methods:**

```python
collector = BenchmarkDiagnosticsCollector()

# Collect each incident
collector.collect_incident(
    seed=42,
    incident_idx=0,
    signal={"incident_id": "...", "ts": "...", "trigger": "...", "service": "..."},
    ground_truth={"family": 5, "expected_remediation": "restart_worker", ...},
    context={"related_events": [...], "similar_past_incidents": [...], ...},
    latency_ms=42.3
)

# Export to JSON
collector.export_json("diagnostics.json")

# Export to CSV (flat view)
collector.export_csv("diagnostics.csv")

# Compute failure stats
stats = collector.compute_failure_stats()
# Returns: {total_incidents, recall_miss, precision_contamination, 
#           remediation_mismatch, decoy_false_positive, correct, failure_summary}
```

**CSV Export Columns:**
- `seed`, `incident_idx`, `incident_id`, `latency_ms`
- `true_family`, `is_decoy`, `canonical_service`, `expected_remediation`
- `pred_top_5_families`, `pred_top_5_similarities`
- `pred_num_confident_matches`
- `top_remediation_action`, `top_remediation_confidence`
- `match_outcome`
- `has_deploy`, `has_metric`, `has_trace_or_log`, `has_remediation`, `total_events`

---

### 3. `failure_analysis.py` — Comprehensive Failure Breakdown

**Class:** `FailureAnalyzer`

Loads diagnostic JSON and computes detailed failure matrices and statistics.

**Usage:**
```python
from failure_analysis import FailureAnalyzer

analyzer = FailureAnalyzer("diagnostics.json")

# Confusion matrix: predicted_family → true_family → count
confusion = analyzer.compute_confusion_matrix()
# {1: {5: 2, 1: 4, 4: 2}, 5: {4: 1, 1: 4, ...}, ...}

# Contamination matrix: true_family → contaminating_family → count
contamination = analyzer.compute_contamination_matrix()

# False positive frequencies by predicted family
fp_freq = analyzer.false_positive_frequencies()
# {1: 12, 5: 3, ...}

# Top 10 recurring wrong-family substitutions
substitutions = analyzer.family_substitution_stats(top_n=10)
# [{true_family: 1, wrong_family: 5, count: 4}, ...]

# Precision@5 breakdown by true family
precision_by_fam = analyzer.precision_decay_by_family()
# {1: {num_incidents, correct_rank1, recall, precision@5, rank1_accuracy}, ...}

# Remediation failure analysis
remed_stats = analyzer.remediation_mismatches()
# {total_with_expected_remediation, exact_matches, wrong_action, 
#  confidence_above_threshold_but_wrong, mismatches_by_family}

# Decoy rejection analysis
decoy_stats = analyzer.decoy_failure_analysis()
# {total_decoys, correctly_rejected, false_positives, decoy_recall,
#  avg_similarity_when_rejected, avg_similarity_when_fp, false_positive_rate}

# Export all analysis to JSON
analyzer.export_json("failure_analysis.json")
```

**Output Structure (failure_analysis.json):**
```json
{
  "metadata": {...},
  "confusion_matrix": {
    "predicted_family": {"true_family": count, ...}
  },
  "contamination_matrix": {
    "true_family": {"contaminating_family": count, ...}
  },
  "false_positive_frequencies": {
    "family": count
  },
  "top_substitutions": [
    {"true_family": 1, "wrong_family": 5, "count": 4}
  ],
  "precision_by_family": {
    "family": {
      "num_incidents": 10,
      "correct_rank1": 7,
      "correct_in_top5": 9,
      "recall": 0.9,
      "precision@5": 0.78,
      "rank1_accuracy": 0.7
    }
  },
  "remediation_analysis": {
    "total_with_expected_remediation": 50,
    "exact_matches": 42,
    "wrong_action": 5,
    "missing_action": 3,
    "confidence_above_threshold_but_wrong": 2,
    "mismatches_by_family": {}
  },
  "decoy_analysis": {
    "total_decoys": 10,
    "correctly_rejected": 8,
    "false_positives": 2,
    "decoy_recall": 0.8,
    "avg_similarity_when_rejected": 0.32,
    "avg_similarity_when_fp": 0.68,
    "false_positive_rate": 0.2
  }
}
```

---

### 4. `benchmark_score_attribution.py` — Score Loss Attribution

**Class:** `ScoreAttributionAnalyzer`

Maps each incident to the exact reason(s) score was lost.

**Usage:**
```python
from benchmark_score_attribution import ScoreAttributionAnalyzer

analyzer = ScoreAttributionAnalyzer("diagnostics.json")

# Analyze single incident
analysis = analyzer.analyze_incident(incident_dict)
# Returns: {incident_id, loss_category, loss_magnitude, details}

# Loss categories:
# - "no_loss": incident scored correctly
# - "recall_miss": true family not in top-5
# - "precision_contamination": wrong family in top-5
# - "remediation_mismatch": right family but wrong remediation
# - "decoy_false_positive": decoy matched with confidence >= 0.5

# Aggregate loss breakdown across all incidents
breakdown = analyzer.aggregate_loss_breakdown()
# {per_category: {loss_category: {count, total_loss_points, avg_loss}},
#  total_incidents, total_loss_points, avg_loss_per_incident}

# Top 20 highest-impact failures
failures = analyzer.highest_impact_failures(top_n=20)
# [{incident_id, seed, loss_category, loss_magnitude, details}, ...]

# Loss aggregated by true family
loss_by_fam = analyzer.loss_by_family()
# {family: {num_incidents, loss_count, loss_rate, loss_categories}, ...}

# Confidence calibration analysis
calibration = analyzer.confidence_calibration_analysis()
# {high_confidence_but_wrong, low_confidence_but_correct,
#  mean_confidence_when_correct, mean_confidence_when_wrong,
#  confidence_separation}

# Export comprehensive analysis
analyzer.export_json("score_attribution.json")
```

**Output Structure (score_attribution.json):**
```json
{
  "metadata": {...},
  "loss_breakdown": {
    "per_category": {
      "recall_miss": {"count": 30, "total_loss_points": 30.0, "avg_loss_per_incident": 1.0},
      "remediation_mismatch": {"count": 5, "total_loss_points": 5.0, ...},
      ...
    },
    "total_incidents": 100,
    "total_loss_points": 42.0,
    "avg_loss_per_incident": 0.42
  },
  "loss_by_family": {
    "family": {
      "num_incidents": 15,
      "loss_count": 6,
      "loss_rate": 0.4,
      "loss_categories": {"recall_miss": 4, "remediation_mismatch": 2}
    }
  },
  "confidence_calibration": {
    "high_confidence_but_wrong": 5,
    "low_confidence_but_correct": 8,
    "mean_confidence_when_correct": 0.75,
    "mean_confidence_when_wrong": 0.42,
    "confidence_separation": 0.33
  },
  "highest_impact_failures": [
    {
      "incident_id": "inc-123-5",
      "loss_category": "recall_miss",
      "loss_magnitude": 1.0,
      "details": {
        "true_family": 5,
        "predicted_rank1": 2,
        "top_5_families": [2, 1, 3, 7, 4],
        "top_5_similarities": [0.81, 0.62, 0.55, 0.48, 0.42]
      }
    }
  ]
}
```

---

## Integration with `bench_run.py`

To integrate diagnostic collection into the benchmark, extend `_run_one_seed()` in `harness.py`:

```python
from benchmark_diagnostics import BenchmarkDiagnosticsCollector

def _run_one_seed(adapter_factory, cfg, mode, warmup):
    """Run a single seed with diagnostic collection."""
    adapter = adapter_factory()
    ds = generate(cfg)
    
    adapter.ingest(ds.train_events)
    adapter.ingest(ds.eval_events)
    
    # Initialize collector
    diag_collector = BenchmarkDiagnosticsCollector()
    
    scores = []
    for idx, (sig, gt) in enumerate(zip(ds.eval_signals, ds.ground_truth)):
        signal = {...}
        q0 = time.monotonic()
        ctx = adapter.reconstruct_context(signal, mode=mode)
        latency = (time.monotonic() - q0) * 1000.0
        
        # Collect diagnostics
        diag_collector.collect_incident(
            seed=cfg.seed,
            incident_idx=idx,
            signal=signal,
            ground_truth=gt,
            context=ctx,
            latency_ms=latency
        )
        
        # Score normally
        in_top_k, precision = score_match(ctx, gt, k=5)
        rem_ok = score_remediation(ctx, gt)
        scores.append(...)
    
    # Export diagnostics
    diag_collector.export_json(f"diagnostics_seed_{cfg.seed}.json")
    diag_collector.export_csv(f"diagnostics_seed_{cfg.seed}.csv")
    
    # Return results with diagnostics attached
    return {
        ...results,
        "diagnostics_file": f"diagnostics_seed_{cfg.seed}.json"
    }
```

---

## Workflow Example

### Step 1: Run Benchmark with Diagnostics
```bash
python bench_run.py --seeds 42 101 999 --out report.json
```

### Step 2: Aggregate Diagnostics Across Seeds
```python
import json
from pathlib import Path
from benchmark_diagnostics import BenchmarkDiagnosticsCollector

# Merge all seed diagnostics
master_collector = BenchmarkDiagnosticsCollector()

for seed_diag_file in Path(".").glob("diagnostics_seed_*.json"):
    with open(seed_diag_file) as f:
        data = json.load(f)
        for incident in data["incidents"]:
            master_collector.incidents.append(incident)

master_collector.export_json("diagnostics_all.json")
master_collector.export_csv("diagnostics_all.csv")
print(master_collector.compute_failure_stats())
```

### Step 3: Analyze Failures
```python
from failure_analysis import FailureAnalyzer

analyzer = FailureAnalyzer("diagnostics_all.json")
analyzer.export_json("failure_analysis.json")

# Quick summary
print("Family-level precision:")
for fam, stats in analyzer.precision_decay_by_family().items():
    print(f"  Family {fam}: recall={stats['recall']:.2f}, precision@5={stats['precision@5']:.3f}")

print("\nTop substitutions (wrong family matches):")
for sub in analyzer.family_substitution_stats(top_n=5):
    print(f"  True {sub['true_family']} → Pred {sub['wrong_family']}: {sub['count']} times")

print("\nDecoy performance:")
decoy = analyzer.decoy_failure_analysis()
print(f"  Decoy recall: {decoy['decoy_recall']:.2f}")
print(f"  False positive rate: {decoy['false_positive_rate']:.2f}")
```

### Step 4: Attribute Score Loss
```python
from benchmark_score_attribution import ScoreAttributionAnalyzer

scorer = ScoreAttributionAnalyzer("diagnostics_all.json")
scorer.export_json("score_attribution.json")

# Loss breakdown
breakdown = scorer.aggregate_loss_breakdown()
print("Score Loss Breakdown:")
for cat, stats in breakdown["per_category"].items():
    print(f"  {cat}: {stats['count']} incidents, {stats['total_loss_points']:.1f} points")

print("\nHighest-impact failures:")
for failure in scorer.highest_impact_failures(top_n=5):
    print(f"  {failure['incident_id']}: {failure['loss_category']}")

# Confidence analysis
calib = scorer.confidence_calibration_analysis()
print(f"\nConfidence separation: {calib['confidence_separation']:.3f}")
print(f"High-conf errors: {calib['high_confidence_but_wrong']}")
```

---

## Key Metrics Explained

### Recall@5
- **Definition:** Fraction of incidents where true family appears in top-5
- **Loss Mode:** `recall_miss` — true family not in top-5 at all

### Precision@5
- **Definition:** Mean fraction of top-5 matches that are correct (for that incident)
- **Loss Mode:** `precision_contamination` — wrong families ranked high

### Remediation Accuracy
- **Definition:** Fraction of incidents where top remediation matches ground truth
- **Loss Mode:** `remediation_mismatch` — right family but wrong action

### Decoy Handling
- **Definition:** Decoys should return no matches with similarity ≥ 0.5
- **Loss Mode:** `decoy_false_positive` — decoy matched with high confidence

---

## Design Principles

1. **Deterministic:** No randomness; same input → same diagnostics
2. **Benchmark-Safe:** Read-only analysis; non-invasive
3. **Zero ML:** Pure Python; no external ML libs
4. **Incident-Level:** Each diagnostic record is independently interpretable
5. **Traceable:** Full attribution path from incident to loss reason
6. **Production-Ready:** Clean, tested, idiomatic Python

---

## Files Generated

| File | Purpose | Format |
|------|---------|--------|
| `diagnostics.json` | Full per-incident diagnostics | JSON, ~10-20 MB for full bench |
| `diagnostics.csv` | Flat view for spreadsheet analysis | CSV, 1 row/incident |
| `failure_analysis.json` | Confusion matrices, family stats | JSON |
| `score_attribution.json` | Loss attribution and calibration | JSON |

---

## Tips for Analysis

### Finding Quick Wins
1. Look at `top_substitutions` — are certain families confusing?
2. Check `remediation_analysis` — is remediation selection weak?
3. Review `decoy_analysis` — how many false positives?

### Debugging Precision
- Use `contamination_matrix` to see which families contaminate top-5
- Check `confidence_calibration` — is the engine over/under-confident?

### Debugging Recall
- Use `confusion_matrix` to see what families are predicted instead
- Review `highest_impact_failures` to find pattern

### Performance Tuning
- CSV export enables pivot tables in Excel/Pandas
- Group by `true_family` or `match_outcome` to isolate issues
- Filter by `has_deploy`, `has_metric`, etc. to understand evidence availability

