# ⚡ Quick Reference: Benchmark Optimization Framework

**Status:** All systems ready. 17/17 modules in place.  
**Current Score:** 50.4% (0.4036 / 0.8000)  
**Target:** 60%+ (0.48+ / 0.8000)

---

## 🎯 5-Minute Start

```bash
# Verify everything works
python run_optimization.py --mode validate \
  --config default_config.json \
  --seeds 9999 \
  --output results/test/
```

---

## 📋 The 5-Phase Workflow

### **Phase 1: Diagnose (30 min)**
```bash
python run_optimization.py --mode full \
  --seeds 9999 31415 27182 \
  --output results/phase1/
```
**Then analyze:** `results/phase1/score_attribution.json`  
**Find:** Which loss category (recall_miss, precision_contamination, remediation_mismatch, decoy_failure) is biggest

### **Phase 2: Sweep (1-2 hours)**
```bash
# For precision issues
python run_optimization.py --mode sweep \
  --param stageA_min_similarity 0.50 0.55 0.60 0.65 0.70 \
  --param decoy_cap_similarity 0.35 0.40 0.45 0.50 \
  --seeds 9999 31415 \
  --output results/phase2/

# For recall issues
python run_optimization.py --mode sweep \
  --param same_cid_boost 0.10 0.20 0.30 0.40 \
  --param graph_distance_penalty 0.02 0.05 0.10 0.15 \
  --seeds 9999 31415 \
  --output results/phase2/

# For remediation issues
python run_optimization.py --mode sweep \
  --param remediation_confidence_blend 0.3 0.5 0.7 \
  --seeds 9999 31415 \
  --output results/phase2/
```
**Find:** `results/phase2/sweep_results.json`  
**Look for:** `best_config` with highest `best_score`

### **Phase 3: Targeted (1-2 hours)**
```bash
python run_optimization.py --mode targeted \
  --metric precision@5_mean \
  --initial results/phase2/best_config.json \
  --seeds 9999 31415 27182 \
  --output results/phase3/
```

### **Phase 4: Ablation (1 hour)**
```bash
python run_optimization.py --mode ablation \
  --baseline default_config.json \
  --variants results/phase2/best_config.json \
             results/phase3/best_config.json \
  --seeds 9999 31415 27182 \
  --output results/phase4/
```

### **Phase 5: Validate (30 min)**
```bash
python run_optimization.py --mode validate \
  --config results/phase3/best_config.json \
  --seeds 9999 31415 27182 16180 11235 \
  --output results/phase5/
```

---

## 🔧 Key Parameters

| Parameter | Range | Effect |
|-----------|-------|--------|
| `stageA_min_similarity` | 0.50-0.70 | Stage-A precision threshold |
| `decoy_cap_similarity` | 0.35-0.50 | Decoy similarity cap |
| `decoy_cap_remediation` | 0.25-0.45 | Decoy remediation cap |
| `same_cid_boost` | 0.10-0.30 | Canonical service bonus |
| `cross_cid_penalty` | 0.05-0.20 | Cross-service penalty |
| `graph_distance_penalty` | 0.02-0.15 | Distance attenuation |
| `action_success_weight` | 0.05-0.20 | Action success rate weight |
| `evidence_boost` | 0.15-0.35 | Evidence agreement bonus |
| `topology_neighbor_boost` | 0.05-0.15 | Neighbor overlap bonus |
| `remediation_confidence_blend` | 0.3-0.7 | Empirical vs posterior ratio |

---

## 📊 Outputs

Each run generates:

| File | Content |
|------|---------|
| `{prefix}_report.json` | Harness output (aggregated metrics) |
| `{prefix}_diagnostics.json` | Per-incident diagnostics |
| `{prefix}_diagnostics.csv` | Flat diagnostic view |
| `{prefix}_failure_analysis.json` | Failure patterns |
| `{prefix}_score_attribution.json` | Loss breakdown by category |
| `sweep_results.json` | All configs ranked by score |
| `ablation_report.json` | Delta comparisons |

---

## 🎯 Diagnostics to Action

**If `recall_miss` is high:**
- Use family deduplication (lower `same_cid_boost`)
- Increase evidence weight
- Decrease distance penalties

**If `precision_contamination` is high:**
- Increase `stageA_min_similarity` (0.60-0.70)
- Increase `decoy_cap_similarity` cap
- Decrease canonical boost

**If `remediation_mismatch` is high:**
- Focus on `remediation_confidence_blend`
- Check family aggregation
- Review success-rate weighting

**If `decoy_failure` is high:**
- Aggressive suppression: `decoy_cap_*` ≤ 0.40
- Check evidence requirements
- Consider stage-A filters

---

## ⚡ Shortcut Commands

```bash
# Quick test (single seed)
python run_optimization.py --mode validate --seeds 9999

# Fast sweep (2 seeds, fast mode)
python run_optimization.py --mode sweep --mode-fast \
  --seeds 9999 31415 \
  --param stageA_min_similarity 0.50 0.60 0.70

# Validate best config
python run_optimization.py --mode validate \
  --config results/best_config.json

# Compare configs
python run_optimization.py --mode ablation \
  --baseline default_config.json \
  --variants my_config.json
```

---

## 📈 Expected Results

| Phase | Expected Score | Status |
|-------|----------------|--------|
| Baseline | 0.4036 (50.4%) | Current ✅ |
| Phase 2 Sweep | 0.42-0.44 (52-55%) | +1-2% |
| Phase 3 Targeted | 0.44-0.46 (55-57%) | +2% |
| Phase 4 Ablation | 0.46-0.48 (57-60%) | +3% |
| Phase 5 Validate | 0.48+ (60%+) | **Target** 🎯 |

---

## 🚨 Troubleshooting

| Problem | Solution |
|---------|----------|
| Slow runs | Add `--mode-fast` flag |
| No improvement | Review Phase 1 diagnostics carefully |
| Import errors | Check all 17 modules in `/Users/apple/SRE/` |
| Metric regression | Check ablation results, may be over-optimizing |
| Decoy failures | Decrease `decoy_cap_*` parameters further |

---

## 📂 All 17 Modules

**Diagnostics (4):**
- `diagnostic_extractor.py`
- `benchmark_diagnostics.py`
- `failure_analysis.py`
- `benchmark_score_attribution.py`

**Optimization (5):**
- `weight_sweep_framework.py`
- `two_stage_retrieval.py`
- `remediation_optimizer.py`
- `family_representation.py`
- `decoy_suppression.py`

**Integration (8):**
- `optimized_engine_adapter.py`
- `optimized_bench_runner.py`
- `ablation_study_framework.py`
- `seed_wise_comparison.py`
- `experiment_runner.py`
- `config_manager.py`
- `run_optimization.py`
- Plus 2 framework support files

---

## 🏁 Success Checklist

Before finalizing:
- [ ] Phase 1 identifies clear bottleneck
- [ ] Phase 2 finds improving configs
- [ ] Phase 3 pushes weak metrics up
- [ ] Phase 4 validates no regressions
- [ ] Phase 5 shows score > 0.4036

---

## 📞 Documentation

- **Start here:** `GETTING_STARTED.md` (5 phases explained)
- **Deep dive:** `OPTIMIZATION_FRAMEWORK_SUMMARY.md` (complete overview)
- **Part 1:** `DIAGNOSTIC_FRAMEWORK_README.md`
- **Part 2:** `PART2_INTEGRATION_GUIDE.md`
- **Part 3:** `PART3_INTEGRATION_GUIDE.md`

---

**Time to first result: 30 minutes**  
**Time to 60%: 6-8 hours**  
**Deterministic: Yes ✅**  
**Ready to use: Yes ✅**

🚀 Start Phase 1 now!
