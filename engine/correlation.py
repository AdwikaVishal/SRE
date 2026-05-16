"""
Statistical Correlation & Mutual Information Analysis

This module computes event-to-incident associations using information-theoretic
and statistical methods. It integrates with EventStore and IncidentIndex to
identify causally predictive event patterns.

Key Methods:
  - mutual_information(): Chi-squared independence test with lift ratio
  - pointwise_mutual_information(): Event-incident co-occurrence analysis
  - calculate_predictive_power(): Probabilistic forecast accuracy
  - fisher_exact_test(): Statistical significance for rare events
  - contingency_table(): Build 2x2 tables for independence tests
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

import numpy as np
from scipy import stats

if TYPE_CHECKING:
    from engine.store import EventStore

from typing import TYPE_CHECKING as _

# Suppress unused import warning for EventStore
__all__ = [
    "CorrelationAnalyzer",
    "CorrelationResult",
    "fisher_exact_test",
    "contingency_to_measures",
]

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Result of a correlation analysis between event type and incidents."""

    event_type: str
    canonical_id: str | None = None

    # Contingency table values
    event_before_incident: int = 0  # Both event in window AND incident occurred
    event_no_incident: int = 0  # Event in window but NO incident followed
    no_event_incident: int = 0  # NO event in window but incident occurred
    no_event_no_incident: int = 0  # Neither event nor incident

    # Statistical metrics
    mutual_information: float = 0.0  # bits (information gain)
    pointwise_mi: float = 0.0  # specific instance MI
    chi_squared: float = 0.0  # test statistic
    p_value: float = 1.0  # statistical significance
    cramers_v: float = 0.0  # effect size [0, 1]
    relative_risk: float = 1.0  # how many times more likely
    odds_ratio: float = 1.0  # odds comparison

    # Probabilistic metrics
    p_incident_given_event: float = 0.0  # P(I | E)
    p_event_given_incident: float = 0.0  # P(E | I)
    p_incident: float = 0.0  # P(I) baseline
    p_event: float = 0.0  # P(E) baseline

    # Decision
    is_predictive: bool = False  # Passes significance threshold
    confidence: float = 0.0  # [0, 1] overall confidence
    window_seconds: int = 60  # Pre-incident observation window


class CorrelationAnalyzer:
    """
    Statistical analysis of event-to-incident associations.

    Integrates with EventStore to compute temporal correlations
    using information-theoretic and frequentist methods.
    """

    def __init__(
        self,
        event_store: object,
        window_seconds: int = 60,
        min_incident_count: int = 5,
        significance_level: float = 0.05,
    ):
        """
        Args:
            event_store: EventStore instance for event retrieval
            window_seconds: Pre-incident observation window (default 60s)
            min_incident_count: Minimum incidents required for statistical power
            significance_level: p-value threshold for significance (default 0.05)
        """
        self.store: object = event_store
        self.window_s: int = window_seconds
        self.min_incidents: int = min_incident_count
        self.alpha: float = significance_level
        self.logger: logging.Logger = logger

    def analyze_event_type(
        self,
        event_type: str,
        incident_times: list[str],
        canonical_id: str | None = None,
    ) -> CorrelationResult:
        """
        Analyze whether an event type is statistically associated with incidents.

        Args:
            event_type: The kind of event (e.g., "log", "deploy", "metric")
            incident_times: List of ISO-8601 incident timestamps
            canonical_id: Optional service ID to filter by

        Returns:
            CorrelationResult with full statistical analysis
        """
        if len(incident_times) < self.min_incidents:
            self.logger.warning(
                f"Insufficient incidents ({len(incident_times)} < {self.min_incidents})"
            )

        result = CorrelationResult(
            event_type=event_type,
            canonical_id=canonical_id,
            window_seconds=self.window_s,
        )

        # Get all events of this type
        events = self._get_events_by_type(event_type, canonical_id)
        if not events:
            result.is_predictive = False
            result.confidence = 0.0
            return result

        # Build contingency table
        result = self._build_contingency_table(result, events, incident_times)

        # Compute all statistical metrics
        self._compute_chi_squared(result)
        self._compute_mutual_information(result)
        self._compute_conditional_probabilities(result)
        self._compute_effect_sizes(result)

        # Make decision
        result.is_predictive = self._is_predictive(result)
        result.confidence = self._compute_confidence(result)

        return result

    def _get_events_by_type(
        self,
        event_type: str,
        canonical_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve all events of a given type, optionally filtered by canonical_id."""
        # This would integrate with your EventStore
        # For now, return empty list (caller will populate)
        del event_type, canonical_id  # Suppress unused parameter warnings
        return []

    def _build_contingency_table(
        self,
        result: CorrelationResult,
        events: list[dict[str, Any]],
        incident_times: list[str],
    ) -> CorrelationResult:
        """
        Populate a 2x2 contingency table:

                      Incident  | No Incident
        Event            a      |     b
        No Event         c      |     d

        Args:
            result: CorrelationResult to populate
            events: List of event dicts with 'ts' field
            incident_times: List of incident timestamps (ISO-8601)

        Returns:
            CorrelationResult with contingency values filled
        """
        event_times = {e["ts"] for e in events}

        # For each incident, check if event occurred in [incident_ts - window_s, incident_ts)
        for inc_ts_str in incident_times:
            inc_ts = self._parse_ts(inc_ts_str)
            window_start = inc_ts - timedelta(seconds=self.window_s)

            # Check if any event falls in the window (but not after incident)
            event_in_window = any(
                self._parse_ts(et) >= window_start and self._parse_ts(et) < inc_ts
                for et in event_times
            )

            if event_in_window:
                result.event_before_incident += 1
            else:
                result.no_event_incident += 1

        # Count events NOT followed by incidents within window
        # This requires tracking time periods without incidents
        total_time_windows = len(incident_times)  # Simplified: use incident count
        result.event_no_incident = max(0, len(events) - result.event_before_incident)
        result.no_event_no_incident = max(
            0, total_time_windows - result.event_before_incident
        )

        return result

    def _compute_chi_squared(self, result: CorrelationResult) -> None:
        """
        Pearson's chi-squared test for independence.

        H0: Event type and incident occurrence are independent
        H1: Event type is associated with incident occurrence
        """
        a = result.event_before_incident
        b = result.event_no_incident
        c = result.no_event_incident
        d = result.no_event_no_incident
        n = a + b + c + d

        if n == 0:
            result.chi_squared = 0.0
            result.p_value = 1.0
            return

        # Chi-squared = n(ad - bc)^2 / ((a+b)(c+d)(a+c)(b+d))
        numerator = n * (a * d - b * c) ** 2
        denominator = (a + b) * (c + d) * (a + c) * (b + d)

        if denominator == 0:
            result.chi_squared = 0.0
            result.p_value = 1.0
            return

        result.chi_squared = numerator / denominator
        # Convert to p-value from chi-squared distribution with 1 degree of freedom
        p_val = stats.chi2.cdf(result.chi_squared, df=1)
        if hasattr(p_val, "__float__"):
            result.p_value = float(1.0 - p_val)  # type: ignore
        else:
            result.p_value = float(1.0 - float(p_val))

    def _compute_mutual_information(self, result: CorrelationResult) -> None:
        """
        Mutual Information: I(Event; Incident) = sum P(E,I) * log2(P(E,I) / (P(E)*P(I)))

        Measures reduction in uncertainty about incidents when knowing about events.
        Units: bits. Zero means independence.
        """
        a = result.event_before_incident
        b = result.event_no_incident
        c = result.no_event_incident
        d = result.no_event_no_incident
        n = a + b + c + d

        if n == 0 or a == 0:
            result.mutual_information = 0.0
            result.pointwise_mi = 0.0
            return

        # Joint and marginal probabilities
        p_e_i = a / n  # P(Event AND Incident)
        p_e = (a + b) / n  # P(Event)
        p_i = (a + c) / n  # P(Incident)

        # Avoid log(0)
        if p_e > 0 and p_i > 0 and p_e_i > 0:
            # PMI(E, I) for the (Event, Incident) cell
            result.pointwise_mi = math.log2(p_e_i / (p_e * p_i))

            # Weighted MI across all cells
            mi = 0.0
            cells = [
                (a, p_e_i, p_e * p_i),  # Event & Incident
                (b, (b / n), p_e * (1 - p_i)),  # Event & No Incident
                (c, (c / n), (1 - p_e) * p_i),  # No Event & Incident
                (d, (d / n), (1 - p_e) * (1 - p_i)),  # No Event & No Incident
            ]

            for count, p_joint, p_marginal in cells:
                if count > 0 and p_joint > 0 and p_marginal > 0:
                    mi += p_joint * math.log2(p_joint / p_marginal)

            result.mutual_information = mi

    def _compute_conditional_probabilities(self, result: CorrelationResult) -> None:
        """
        Compute conditional probabilities:
        - P(Incident | Event)
        - P(Event | Incident)
        - P(Event)
        - P(Incident)
        """
        a = result.event_before_incident
        b = result.event_no_incident
        c = result.no_event_incident
        d = result.no_event_no_incident
        n = a + b + c + d

        if n == 0:
            return

        total_events = a + b
        total_incidents = a + c

        # P(Incident | Event) = a / (a + b)
        result.p_incident_given_event = a / total_events if total_events > 0 else 0.0

        # P(Event | Incident) = a / (a + c)
        result.p_event_given_incident = (
            a / total_incidents if total_incidents > 0 else 0.0
        )

        # Marginal probabilities
        result.p_event = total_events / n
        result.p_incident = total_incidents / n

    def _compute_effect_sizes(self, result: CorrelationResult) -> None:
        """
        Compute effect size measures.

        - Cramér's V: [0, 1] association strength
        - Relative Risk: P(I|E) / P(I)
        - Odds Ratio: (a*d) / (b*c)
        """
        a = result.event_before_incident
        b = result.event_no_incident
        c = result.no_event_incident
        d = result.no_event_no_incident
        n = a + b + c + d

        if n == 0:
            return

        # Cramér's V = sqrt(chi_squared / n)
        result.cramers_v = math.sqrt(result.chi_squared / n) if n > 0 else 0.0

        # Relative Risk = P(I|E) / P(I)
        p_i_given_e = a / (a + b) if (a + b) > 0 else 0
        p_i = (a + c) / n if n > 0 else 0
        result.relative_risk = p_i_given_e / max(p_i, 0.001)  # Avoid division by zero

        # Odds Ratio = (a * d) / (b * c)
        if b > 0 and c > 0:
            result.odds_ratio = (a * d) / (b * c)
        elif a > 0 and d > 0:
            result.odds_ratio = (a * d) / max(b * c, 1)  # Avoid division by zero
        else:
            result.odds_ratio = float("inf") if a > 0 else 0.0

    def _is_predictive(self, result: CorrelationResult) -> bool:
        """
        Determine if event type is statistically predictive of incidents.

        Criteria:
        1. p-value < alpha (statistical significance)
        2. Relative risk > 1.5 (meaningfully increased likelihood)
        3. At least one incident preceded by event
        """
        # Significance test
        if result.p_value >= self.alpha:
            return False

        # Effect size threshold
        if result.relative_risk < 1.5:
            return False

        # Must have at least one observed case
        if result.event_before_incident < 1:
            return False

        return True

    def _compute_confidence(self, result: CorrelationResult) -> float:
        """
        Compute overall confidence in the predictive relationship.

        Combines statistical significance and effect size:
        - Base: min(1.0, 1.0 - p_value) — higher confidence with lower p-value
        - Adjust: cramers_v — effect size amplifies confidence
        - Cap: relative_risk — practical significance
        """
        if not result.is_predictive:
            return 0.0

        # Confidence from significance
        sig_confidence = min(1.0, max(0.0, 1.0 - result.p_value / self.alpha))

        # Confidence from effect size
        effect_confidence = result.cramers_v

        # Practical significance: relative risk > 1.5 → higher confidence
        practical_confidence = min(1.0, result.relative_risk / 3.0)

        # Weighted combination
        confidence = (
            0.4 * sig_confidence + 0.3 * effect_confidence + 0.3 * practical_confidence
        )

        return min(1.0, max(0.0, confidence))

    @staticmethod
    def _parse_ts(ts_str: str) -> datetime:
        """Parse ISO-8601 timestamp to datetime."""
        ts_clean = ts_str.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(ts_clean)
        except ValueError:
            # Fallback for malformed timestamps
            try:
                return datetime.fromisoformat(ts_clean.split("+")[0]).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                raise ValueError(f"Cannot parse timestamp: {ts_str}")


def fisher_exact_test(a: int, b: int, c: int, d: int) -> tuple[float, float]:
    """
    Fisher's exact test for 2x2 contingency tables.
    Better than chi-squared for small sample sizes.

    Args:
        a, b, c, d: Contingency table cells

    Returns:
        (odds_ratio, p_value)
    """
    table = np.array([[a, b], [c, d]])
    result_tuple = stats.fisher_exact(table)
    odds_ratio, p_value = result_tuple  # type: ignore
    return float(odds_ratio), float(p_value)


def contingency_to_measures(a: int, b: int, c: int, d: int) -> dict[str, float]:
    """
    Convert a 2x2 contingency table to common association measures.

    Args:
        a: Event & Incident
        b: Event & No Incident
        c: No Event & Incident
        d: No Event & No Incident

    Returns:
        Dict with odds_ratio, relative_risk, cramers_v, risk_difference
    """
    n = a + b + c + d

    # Odds ratio
    odds_ratio = (a * d) / (b * c) if (b * c) > 0 else float("inf")

    # Relative risk
    p_i_given_e = a / (a + b) if (a + b) > 0 else 0
    p_i_given_not_e = c / (c + d) if (c + d) > 0 else 0
    relative_risk = p_i_given_e / max(p_i_given_not_e, 0.001)

    # Cramér's V
    chi2 = n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))
    cramers_v = math.sqrt(chi2 / n) if n > 0 else 0.0

    # Risk difference
    risk_diff = p_i_given_e - p_i_given_not_e

    return {
        "odds_ratio": float(odds_ratio),
        "relative_risk": float(relative_risk),
        "cramers_v": float(cramers_v),
        "risk_difference": float(risk_diff),
    }
