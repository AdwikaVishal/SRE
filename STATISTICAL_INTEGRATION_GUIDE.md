# Statistical Integration Guide: Mutual Information & Correlation Analysis

## Overview

This guide documents the statistical integration of rigorous correlation analysis into your SRE incident prediction system. The `CorrelationAnalyzer` replaces naive mutual information calculations with proper frequentist and information-theoretic methods.

## Architecture

```
Engine
├── store (EventStore) → temporal event log
├── correlation_analyzer (CorrelationAnalyzer)
│   ├── Contingency table construction
│   ├── Chi-squared independence tests
│   ├── Mutual information (Shannon entropy)
│   ├── Effect sizes (odds ratio, relative risk, Cramér's V)
│   └── Confidence scoring
└── Other components (graph, motifs, etc.)
```

## Statistical Methods

### 1. Contingency Table Construction

A 2×2 contingency table represents the joint distribution of Event and Incident:

```
                  Incident  |  No Incident
Event                a      |      b
No Event             c      |      d
```

Where:
- **a** = Events observed before incidents (within window)
- **b** = Events observed without incidents following
- **c** = Incidents not preceded by events
- **d** = Time periods with neither events nor incidents

**Implementation:** `_build_contingency_table()`
- Window: Pre-incident observation period (default 60 seconds)
- Boundary: Events must occur strictly before incident (`t_event < t_incident`)
- Counted: One event occurrence per incident (presence/absence, not quantity)

### 2. Pearson's Chi-Squared Test

Tests the null hypothesis: **Event type and incident occurrence are independent**

```
χ² = n(ad - bc)² / ((a+b)(c+d)(a+c)(b+d))

where n = a + b + c + d
```

- **Test Statistic:** χ² ≥ 0 (larger = stronger evidence against H₀)
- **P-value:** Probability of observing this association by chance
- **Interpretation:**
  - p-value < 0.05 → Statistically significant association
  - p-value ≥ 0.05 → No evidence of association (events are independent)

**When to use:**
- Large sample sizes (n > 5)
- Expected frequencies ≥ 5 in each cell

**Implementation:** `_compute_chi_squared()`

### 3. Fisher's Exact Test (Alternative for Small Samples)

For small sample sizes or sparse contingency tables, Fisher's exact test is more reliable:

```
p-value = (a+b)!(c+d)!(a+c)!(b+d)! / (n! a! b! c! d!)
```

- Provides exact p-value (not approximation)
- Suitable for rare events
- Computationally expensive for large tables

**Implementation:** `fisher_exact_test()` (imported from scipy)

### 4. Mutual Information (Shannon Entropy)

Measures reduction in uncertainty about incidents when knowing about events:

```
I(E; I) = Σ P(e,i) log₂(P(e,i) / (P(e) * P(i)))
```

Summed across all four contingency table cells.

**Interpretation:**
- **I = 0 bits:** Complete independence (no information gain)
- **I > 0 bits:** Events provide information about incidents
- **Larger I:** Stronger association
- **Units:** Bits (log₂) for interpretability

**Specific calculation for (E, I) cell:**
```
PMI(E, I) = log₂(P(E, I) / (P(E) * P(I)))
```

**Implementation:** `_compute_mutual_information()`
- Safely handles log(0) cases
- Weights all four cells
- Returns both global MI and pointwise MI

### 5. Conditional Probabilities

```
P(I | E) = a / (a + b)     # Probability of incident given event
P(E | I) = a / (a + c)     # Probability of event given incident
P(E)     = (a + b) / n     # Marginal probability of event
P(I)     = (a + c) / n     # Marginal probability of incident
```

**Interpretation:**
- **P(I | E):** Risk of incident if event occurs
- **P(E | I):** Likelihood event preceded incident (causal support)
- **P(E) and P(I):** Base rates for context

**Implementation:** `_compute_conditional_probabilities()`

### 6. Effect Sizes

#### Odds Ratio (OR)

```
OR = (a * d) / (b * c)
```

Ratio of odds of incident given event vs. given no event.

- **OR = 1:** No association
- **OR > 1:** Event increases odds of incident
- **OR < 1:** Event decreases odds of incident
- **Interpretation:** OR = 3 means "3 times the odds"

#### Relative Risk (RR)

```
RR = P(I | E) / P(I | ¬E)
   = (a / (a+b)) / (c / (c+d))
```

Risk of incident if event occurs vs. if it doesn't.

- **RR = 1:** No association
- **RR > 1:** Event increases risk
- **RR < 1:** Event decreases risk
- **Interpretation:** RR = 2 means "twice as likely"

#### Cramér's V

```
V = √(χ² / n)    where n = a + b + c + d
```

Standardized effect size in [0, 1].

- **V = 0:** No association
- **V = 1:** Perfect association
- **V ≈ 0.1:** Small effect
- **V ≈ 0.3:** Medium effect
- **V ≈ 0.5:** Large effect

**Implementation:** `_compute_effect_sizes()`

### 7. Predictive Classification

An event type is marked **predictive** if it meets all criteria:

1. **Statistical Significance:** p-value < α (default 0.05)
2. **Practical Significance:** Relative Risk > 1.5
3. **Observed Association:** At least one event-incident pair

**Rationale:**
- Criterion 1 filters noise
- Criterion 2 requires meaningful magnitude
- Criterion 3 requires empirical evidence

**Implementation:** `_is_predictive()`

### 8. Confidence Score

Combines statistical and practical significance:

```
confidence = (0.4 * sig_confidence) 
           + (0.3 * effect_confidence) 
           + (0.3 * practical_confidence)

where:
  sig_confidence = 1 - (p_value / α)       [significance]
  effect_confidence = cramers_v             [effect size]
  practical_confidence = min(1, RR / 3)    [practical relevance]
```

**Returns:** [0.0, 1.0]
- 0.0: Not predictive or no evidence
- 0.5–0.7: Moderate confidence
- 0.8+: High confidence

**Implementation:** `_compute_confidence()`

## Usage Guide

### Basic Usage

```python
from engine.correlation import CorrelationAnalyzer
from engine.store import EventStore

# Initialize
store = EventStore()
analyzer = CorrelationAnalyzer(
    event_store=store,
    window_seconds=60,          # Pre-incident observation window
    min_incident_count=5,       # Minimum statistical power
    significance_level=0.05,    # p-value threshold
)

# Analyze an event type
incident_times = [
    "2024-01-01T10:00:00Z",
    "2024-01-01T10:05:00Z",
    "2024-01-01T10:10:00Z",
]

result = analyzer.analyze_event_type(
    event_type="error_log",
    incident_times=incident_times,
    canonical_id=None,  # Optional: filter by service
)

# Inspect results
print(f"Predictive: {result.is_predictive}")
print(f"Confidence: {result.confidence:.2f}")
print(f"Relative Risk: {result.relative_risk:.2f}")
print(f"Mutual Information: {result.mutual_information:.3f} bits")
print(f"P-value: {result.p_value:.4f}")
```

### Advanced: Manual Contingency Table Analysis

```python
from engine.correlation import contingency_to_measures

# If you already have a contingency table:
a, b, c, d = 50, 10, 10, 30  # Event-Incident, Event-NoIncident, etc.

measures = contingency_to_measures(a, b, c, d)
print(f"Odds Ratio: {measures['odds_ratio']:.2f}")
print(f"Relative Risk: {measures['relative_risk']:.2f}")
print(f"Cramér's V: {measures['cramers_v']:.3f}")
print(f"Risk Difference: {measures['risk_difference']:.3f}")
```

### Integration with Engine

The Engine class automatically instantiates CorrelationAnalyzer:

```python
from adapters.engine import Engine

engine = Engine()

# Use correlation analysis during context reconstruction
result = engine.correlation_analyzer.analyze_event_type(
    event_type="deploy",
    incident_times=incident_times,
)
```

## Configuration Parameters

### CorrelationAnalyzer Constructor

| Parameter | Default | Description |
|-----------|---------|-------------|
| `event_store` | Required | EventStore instance |
| `window_seconds` | 60 | Pre-incident observation window (seconds) |
| `min_incident_count` | 5 | Minimum incidents for statistical power |
| `significance_level` | 0.05 | P-value threshold (α) |

### Thresholds for Predictive Classification

| Metric | Threshold | Justification |
|--------|-----------|---------------|
| P-value | < 0.05 | Standard statistical significance |
| Relative Risk | > 1.5 | Requires 50% increased likelihood |
| Event-Incident Pairs | ≥ 1 | Requires empirical observation |

**Tuning advice:**
- Lower significance_level → fewer false positives, higher false negatives
- Higher min_incident_count → more statistical power, but requires more data
- Longer window_seconds → more events captured but risk of spurious correlations

## Interpreting Results

### CorrelationResult Fields

```python
@dataclass
class CorrelationResult:
    # Input metadata
    event_type: str                    # Kind of event analyzed
    canonical_id: str | None           # Service ID (if filtered)
    window_seconds: int                # Pre-incident window
    
    # Contingency table (raw counts)
    event_before_incident: int         # a: both E and I
    event_no_incident: int             # b: E but not I
    no_event_incident: int             # c: I but not E
    no_event_no_incident: int          # d: neither E nor I
    
    # Statistical tests
    chi_squared: float                 # χ² test statistic
    p_value: float                     # [0, 1] significance
    mutual_information: float          # [0, ∞) bits
    pointwise_mi: float                # log-odds ratio
    
    # Effect sizes
    cramers_v: float                   # [0, 1] effect size
    relative_risk: float               # [0, ∞) risk multiplier
    odds_ratio: float                  # [0, ∞) odds multiplier
    
    # Probabilities
    p_incident_given_event: float      # P(I | E)
    p_event_given_incident: float      # P(E | I)
    p_incident: float                  # P(I) base rate
    p_event: float                     # P(E) base rate
    
    # Decision
    is_predictive: bool                # Meets all thresholds
    confidence: float                  # [0, 1] overall confidence
```

### Interpretation Examples

#### Strong Predictor (High Confidence)

```
event_type: "deployment"
is_predictive: True
confidence: 0.89

chi_squared: 25.4
p_value: 0.000001        ← Highly significant
relative_risk: 4.2       ← 4x increased risk
odds_ratio: 8.5          ← 8x higher odds
cramers_v: 0.52          ← Large effect
mutual_information: 1.23 bits

contingency table:
                Incident  No Incident
Deploy             80         20
No Deploy          20         80
```

**Interpretation:** Deployments are strongly predictive of incidents. If a deployment occurs in the 60s before an incident, we can be 89% confident it's causally related.

#### Weak Predictor (Low Confidence)

```
event_type: "routine_log"
is_predictive: False
confidence: 0.12

chi_squared: 0.8
p_value: 0.371          ← Not significant
relative_risk: 1.05     ← Minimal increased risk
odds_ratio: 1.1         ← Negligible odds change
cramers_v: 0.09         ← Negligible effect
mutual_information: 0.001 bits

contingency table:
                Incident  No Incident
Log               110         890
No Log            890        8110
```

**Interpretation:** Routine logs occur at baseline rate with no meaningful association to incidents. Not predictive.

#### Rare but Predictive Event

```
event_type: "config_change"
is_predictive: True
confidence: 0.76

chi_squared: 12.3
p_value: 0.0004         ← Significant
relative_risk: 2.8      ← 2.8x increased risk
odds_ratio: 6.2         ← 6x higher odds
cramers_v: 0.31         ← Medium effect
mutual_information: 0.68 bits

contingency table:
                Incident  No Incident
ConfigChange       12          4
No ConfigChange    60         924
```

**Interpretation:** Config changes are rare but highly predictive when they occur. 75% of the 12 config changes were followed by incidents.

## Common Pitfalls & Solutions

### 1. Small Sample Size

**Problem:** Chi-squared test unreliable when n < 5 in any cell

**Solution:** Use `fisher_exact_test()` instead:
```python
odds_ratio, p_value = fisher_exact_test(a, b, c, d)
```

### 2. Rare Events with No Association

**Problem:** Spurious correlations with very rare events (a=1, b=0, c=0, d=millions)

**Solution:** Require minimum threshold of event-incident pairs:
```python
# In _is_predictive():
if result.event_before_incident < 3:  # Increase minimum
    return False
```

### 3. Long Window = Spurious Causality

**Problem:** 600s window captures unrelated events before incidents

**Solution:** Calibrate `window_seconds` to domain knowledge:
```python
# For fast-moving systems: 30–60s
analyzer = CorrelationAnalyzer(window_seconds=30)

# For slower systems: 120–300s
analyzer = CorrelationAnalyzer(window_seconds=180)
```

### 4. Confounding Variables

**Problem:** Event A and B both precede incidents, but B causes A

**Solution:** Not directly addressed by pairwise analysis. Requires:
- Temporal ordering analysis
- Conditional independence tests
- Causal inference techniques (beyond this module)

## Mathematical Background

### Why Mutual Information Over Simple Ratios?

Original code used: `lift_ratio = P(I|E) / P(I)`

Problems:
- Doesn't account for joint distribution
- Asymmetric (MI(E;I) = MI(I;E))
- Not grounded in information theory

Our approach uses: Mutual Information I(E; I)

Benefits:
- Measures uncertainty reduction in bits
- Symmetric property
- Grounded in Shannon entropy
- Combined with hypothesis tests for significance

### Why Contingency Tables?

Alternatives considered:
1. **Correlation coefficients (Pearson r):** Assumes continuous variables
2. **Lift ratios:** No statistical test, ad-hoc thresholds
3. **Bayesian posterior:** Requires prior specification

Contingency tables + chi-squared:
- ✅ Designed for categorical data
- ✅ Hypothesis testing with p-values
- ✅ Well-understood effect sizes
- ✅ No distributional assumptions

## Performance Considerations

### Time Complexity

- **Contingency table:** O(n_events * n_incidents)
- **Chi-squared:** O(1)
- **Mutual information:** O(1)
- **Overall:** O(n_events * n_incidents) dominated by window scanning

### Optimization Tips

1. **Batch analysis:** Compute all event types together
   ```python
   results = {}
   for event_type in ["log", "deploy", "metric"]:
       results[event_type] = analyzer.analyze_event_type(
           event_type, incidents
       )
   ```

2. **Pre-filter events:** Only analyze relevant time windows
   ```python
   relevant_events = store.get_by_canonical_ids(
       [cid], anchor_ts, window_s=300
   )
   ```

3. **Cache incident times:** Don't re-parse ISO-8601 repeatedly

## Testing

Run the comprehensive test suite:

```bash
pytest tests/test_correlation_analyzer.py -v
```

Test coverage includes:
- Contingency table construction (multiple scenarios)
- Chi-squared independence test
- Mutual information (independence and association cases)
- Conditional probabilities
- Effect sizes (OR, RR, Cramér's V)
- Predictive classification
- Confidence scoring
- Fisher's exact test
- Timestamp parsing
- Full integration pipeline

## References

1. **Pearson's Chi-Squared Test**
   - Pearson, K. (1900). "On the criterion that a given system..."
   - Standard frequentist independence test

2. **Mutual Information / Shannon Entropy**
   - Shannon, C.E. (1948). "A Mathematical Theory of Communication"
   - Information theoretic foundation

3. **Effect Sizes**
   - Cramér, Harald. (1946). Mathematical Methods of Statistics
   - Odds Ratio and Relative Risk: standard epidemiological measures

4. **Fisher's Exact Test**
   - Fisher, Ronald A. (1934). "Statistical methods for research workers"
   - Exact test for small sample contingency tables

## Future Enhancements

1. **Temporal Granularity:** Multiple window sizes (30s, 60s, 300s)
2. **Multivariate Analysis:** Test combinations of event types
3. **Lag Analysis:** Discover optimal time delays
4. **Confounding Adjustment:** Partial correlation or stratification
5. **Causal Inference:** Granger causality or directed acyclic graphs
6. **Visualization:** Heatmaps of event-incident associations

---

**Author:** Statistical Integration Team  
**Date:** 2024  
**Status:** Production Ready
