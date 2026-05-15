# 🚀 GETTING STARTED: Benchmark Optimization Framework

**Current Score:** 50.4% (0.4036 / 0.8000)  
**Target:** >60% (0.48+ / 0.8000)

This guide shows you how to use the complete optimization framework to systematically improve the benchmark score.

---

## 📦 What You Have

The system has 3 major parts, all now ready to use:

### **Part 1: Diagnostic Infrastructure**
- `diagnostic_extractor.py` - Extracts per-incident diagnostics
- `benchmark_diagnostics.py` - Collects diagnostics during benchmark runs
- `failure_analysis.py` - Analyzes failure patterns
- `benchmark_score_attribution.py` - Attributes score loss to failure modes

### **Part 2: Optimization Modules**
- `weight_sweep_framework.py` - Hyperparameter grid search
- `two_stage_retrieval.py` - Smart retrieval with precision/recall tradeoff
- `remediation_optimizer.py` - Optimizes remediation ranking
- `family_representation.py` - Prevents same-family clustering
- `decoy_suppression.py` - Aggressive decoy filtering

### **Part 3: Integration & Orchestration**
- `optimized_engine_adapter.py` - Plugs all Part 2 modules into engine
- `optimized_bench_runner.py` - Runs benchmarks with diagnostics
- `experiment_runner.py` - Orchestrates experiments
- `ablation_study_framework.py` - Compares configurations
- `config_manager.py` - Manages configurations
- `run_optimization.py` - CLI entry point

---

## 🎯 Quick Start: 5-Minute Validation

First, verify everything works with a quick baseline run:

```bash
# Run a single-seed validation to check all systems
python run_optimization.py --mode validate \
  --config default_config.json \
  --seeds 9999 \
  --output results/baseline/

# Expected output:
# - results/baseline/validation_report.json
# - results/baseline/validation_diagnostics.json
# - Results show current score ~0.4036
```

---

## 🔍 Phase 1: Baseline Diagnostic Analysis (30 min)

Understand WHY you're at 50.4%:

```bash
# Run baseline with full diagnostics
python run_optimization.py --mode full \
  --seeds 9999 31415 27182 \
  --output results/baseline_analysis/

# Generates:
# - results/baseline_analysis/full_report.json         (harness output)
# - results/baseline_analysis/diagnostics.json         (per-incident)
# - results/baseline_analysis/failure_analysis.json    (patterns)
# - results/baseline_analysis/score_attribution.json   (loss breakdown)
```

**Analyze the results:**

```python
import json

# Load diagnostic results
with open("results/baseline_analysis/score_attribution.json") as f:
    attribution = json.load(f)

# What's losing the most points?
print(attribution["loss_breakdown"])
# Example output:
# {
#   "recall_miss": 45,      # True family not in top-5
#   "precision_contamination": 38,
#   "remediation_mismatch": 12,
#   "decoy_failure": 8
# }

# What are the top recurring failures?
print(attribution["top_failing_families"])
```

This tells you exactly what to optimize for.

---

## ⚙️ Phase 2: Weight Sweep (1-2 hours)

If diagnostics show **precision contamination** (wrong families in top-5):

```bash
# Sweep key precision-affecting parameters
python run_optimization.py --mode sweep \
  --param stageA_min_similarity 0.50 0.55 0.60 0.65 0.70 \
  --param decoy_cap_similarity 0.35 0.40 0.45 0.50 \
  --seeds 9999 31415 \
  --output results/precision_sweep/
```

If diagnostics show **recall misses** (true family not ranked high):

```bash
# Sweep recall-affecting parameters
python run_optimization.py --mode sweep \
  --param same_cid_boost 0.10 0.15 0.20 0.25 0.30 \
  --param graph_distance_penalty 0.02 0.05 0.10 0.15 \
  --seeds 9999 31415 \
  --output results/recall_sweep/
```

If diagnostics show **remediation mismatches**:

```bash
# Tune remediation aggregation
python run_optimization.py --mode sweep \
  --param remediation_confidence_blend 0.3 0.5 0.7 \
  --seeds 9999 31415 \
  --output results/remediation_sweep/
```

**Analyze sweep results:**

```python
import json

with open("results/precision_sweep/sweep_results.json") as f:
    sweep = json.load(f)

print(f"Best config: {sweep['best_config']}")
print(f"Best score: {sweep['best_score']:.4f}")
print(f"Improvement: +{sweep['best_score'] - 0.4036:.4f}")

# See all results ranked by score
for i, result in enumerate(sweep['all_results'][:5], 1):
    print(f"{i}. {result['score']:.4f} → {result['config']}")
```

---

## 🔬 Phase 3: Targeted Optimization (1-2 hours)

Focus on the weakest metric:

```bash
# Optimize specifically for precision@5_mean
python run_optimization.py --mode targeted \
  --metric precision@5_mean \
  --initial results/precision_sweep/best_config.json \
  --seeds 9999 31415 27182 \
  --output results/precision_optimization/

# OR for remediation_acc
python run_optimization.py --mode targeted \
  --metric remediation_acc \
  --initial results/remediation_sweep/best_config.json \
  --seeds 9999 31415 27182 \
  --output results/remediation_optimization/
```

---

## 📊 Phase 4: Ablation Study (1 hour)

Validate your best config against baseline:

```bash
# Compare best config vs baseline
python run_optimization.py --mode ablation \
  --baseline default_config.json \
  --variants results/precision_sweep/best_config.json \
              results/remediation_sweep/best_config.json \
  --seeds 9999 31415 27182 \
  --output results/ablation/

# Shows metric-by-metric comparison
```

---

## ✅ Phase 5: Final Validation (30 min)

Verify your best config on full seed set:

```bash
# Validate on all 5 default seeds
python run_optimization.py --mode validate \
  --config results/precision_sweep/best_config.json \
  --seeds 9999 31415 27182 16180 11235 \
  --output results/final_validation/

# Check if score improved:
# - If YES (> 0.4036): commit this config
# - If NO: try next best from sweep
```

---

## 📈 Expected Improvement Path

Based on the modular design:

| Phase | Config | Expected Score | Notes |
|-------|--------|----------------|-------|
| Baseline | default | 0.4036 | Current |
| Precision sweep | optimized | 0.42-0.44 | Top-5 diversification |
| Remediation sweep | optimized | 0.44-0.46 | Action ranking |
| Combined | best-of-both | 0.46-0.48 | Should reach 60% range |

---

## 🎛️ Key Parameters to Tune

### For Precision (avoiding wrong families in top-5):
- `stageA_min_similarity` (0.50-0.70): Higher = stricter stage A
- `decoy_cap_similarity` (0.35-0.50): Lower = stronger decoy suppression
- `same_cid_boost` (0.10-0.30): Higher = favor canonical service

### For Recall (getting true family in top-5):
- `graph_distance_penalty` (0.02-0.15): Lower = less distance penalty
- `evidence_boost` (0.15-0.35): Higher = reward evidence
- `topology_neighbor_boost` (0.05-0.15): Higher = favor neighbors

### For Remediation Accuracy:
- `remediation_confidence_blend` (0.3-0.7): Blend of empirical vs posterior
- Adjust through `remediation_optimizer.py` confidence scoring

---

## 📋 Checklist: Full Optimization Campaign

- [ ] Phase 1: Run baseline diagnostics (30 min)
  - [ ] Analyze failure modes
  - [ ] Identify primary loss source
  
- [ ] Phase 2: Run targeted weight sweep (2 hours)
  - [ ] Sweep 1-2 key parameters
  - [ ] Find best config
  
- [ ] Phase 3: Targeted optimization (2 hours)
  - [ ] Focus on weakest metric
  - [ ] Run iterative tuning
  
- [ ] Phase 4: Ablation study (1 hour)
  - [ ] Compare against baseline
  - [ ] Verify improvement
  
- [ ] Phase 5: Final validation (30 min)
  - [ ] Confirm on full seed set
  - [ ] Document best config

**Total time: ~6-7 hours for full campaign**

---

## 🔧 Troubleshooting

**Issue: Validation runs too slow**
```bash
# Use fast mode instead of deep
python run_optimization.py --mode validate \
  --config config.json \
  --mode-fast  # Reduces latency budget 6s → 2s
```

**Issue: Not seeing improvements**
1. Check diagnostics to identify actual bottleneck
2. Make sure sweep covers reasonable parameter ranges
3. Try larger range: `0.30 0.40 0.50 0.60 0.70` instead of `0.50 0.55 0.60`
4. Run more seeds (slower but more stable): `--seeds 9999 31415 27182 16180 11235`

**Issue: Remediation metrics not improving**
- Focus on `family_representation.py` deduplication
- Tune `RemediationOptimizer` confidence blending
- Check that empirical success rates are being aggregated correctly

---

## 📚 Documentation

For detailed information on each component:

- `DIAGNOSTIC_FRAMEWORK_README.md` - How diagnostics work
- `PART2_INTEGRATION_GUIDE.md` - How optimization modules work
- `PART3_INTEGRATION_GUIDE.md` - How orchestration works
- Module docstrings - Class/method documentation

---

## 🎯 Target: 60% (0.48 / 0.8000)

To reach 60%, you need:
- **recall@5** ≥ 0.65 (currently ~0.50-0.55)
- **precision@5_mean** ≥ 0.40 (currently ~0.30-0.35)
- **remediation_acc** ≥ 0.80 (currently ~0.60-0.65)

The framework makes this systematic and measurable. Start with Phase 1 diagnostics to understand your specific bottleneck, then use targeted sweeps to fix it.

---

**Ready? Start with Phase 1:**

```bash
python run_optimization.py --mode full \
  --seeds 9999 31415 27182 \
  --output results/baseline_analysis/
```

Then analyze the failure modes and proceed to the appropriate optimization phase.

Good luck! 🚀
