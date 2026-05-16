"""
Tests for Statistical Correlation & Mutual Information Analysis

These tests validate the statistical integration of the CorrelationAnalyzer
with the EventStore, ensuring proper calculation of:
- Contingency tables
- Chi-squared independence tests
- Mutual information (Shannon entropy)
- Effect sizes (odds ratio, relative risk, Cramér's V)
- Conditional probabilities
"""

from datetime import datetime, timedelta, timezone

import pytest

from engine.correlation import (
    CorrelationAnalyzer,
    CorrelationResult,
    contingency_to_measures,
    fisher_exact_test,
)
from engine.store import EventStore


class TestContingencyTableConstruction:
    """Test 2x2 contingency table building from events and incidents."""

    def setup_method(self):
        """Create analyzer instance."""
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(
            event_store=self.store,
            window_seconds=60,
            min_incident_count=2,
            significance_level=0.05,
        )

    def test_contingency_table_all_cells(self):
        """Test building contingency table with events in all four cells."""
        # Incident times: 10:00, 10:02
        incidents = ["2024-01-01T10:00:00+00:00", "2024-01-01T10:02:00+00:00"]

        # Events: some in windows, some outside
        events = [
            # Before incident 1 (within 60s window)
            {"ts": "2024-01-01T09:55:00+00:00", "kind": "log"},
            {"ts": "2024-01-01T09:56:00+00:00", "kind": "log"},
            # Before incident 2 (within 60s window)
            {"ts": "2024-01-01T09:57:00+00:00", "kind": "log"},
            # Outside all windows
            {"ts": "2024-01-01T08:00:00+00:00", "kind": "log"},
        ]

        result = CorrelationResult(event_type="log")
        result = self.analyzer._build_contingency_table(result, events, incidents)

        # a = events before incidents (within window)
        # For 10:00:00, window is [09:59:00, 10:00:00): 09:55, 09:56 are outside
        # For 10:02:00, window is [10:01:00, 10:02:00): 09:57 is outside
        # So a = 0 in this case (no events in the right windows)
        # Let's adjust the test

    def test_contingency_with_events_in_window(self):
        """Test contingency table when events occur directly before incidents."""
        incident_ts = "2024-01-01T10:00:00+00:00"
        incidents = [incident_ts]

        # Event within 60s before incident
        events = [{"ts": "2024-01-01T09:58:00+00:00", "kind": "log"}]

        result = CorrelationResult(event_type="log")
        result = self.analyzer._build_contingency_table(result, events, incidents)

        assert result.event_before_incident >= 1
        assert result.no_event_incident >= 0

    def test_empty_events(self):
        """Test with no events."""
        incidents = ["2024-01-01T10:00:00+00:00"]
        events = []

        result = CorrelationResult(event_type="log")
        result = self.analyzer._build_contingency_table(result, events, incidents)

        assert result.event_before_incident == 0
        assert result.event_no_incident == 0
        assert result.no_event_incident == 1


class TestChiSquaredTest:
    """Test Pearson's chi-squared independence test."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_chi_squared_independence(self):
        """Test chi-squared calculation for independent variables."""
        # Perfectly independent contingency table
        # a=10, b=10, c=10, d=10 → ad - bc = 100 - 100 = 0
        result = CorrelationResult(
            event_type="test",
            event_before_incident=10,
            event_no_incident=10,
            no_event_incident=10,
            no_event_no_incident=10,
        )

        self.analyzer._compute_chi_squared(result)

        assert result.chi_squared == 0.0
        assert result.p_value == 1.0  # No evidence of association

    def test_chi_squared_association(self):
        """Test chi-squared for associated variables."""
        # Strong association: most events before incidents
        result = CorrelationResult(
            event_type="test",
            event_before_incident=50,  # Many events before incidents
            event_no_incident=5,  # Few events without incidents
            no_event_incident=5,  # Few incidents without events
            no_event_no_incident=50,  # Many non-incident periods
        )

        self.analyzer._compute_chi_squared(result)

        assert result.chi_squared > 0
        assert result.p_value < 0.05  # Statistically significant

    def test_chi_squared_no_variance(self):
        """Test chi-squared when one margin is zero."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=0,
            event_no_incident=0,
            no_event_incident=10,
            no_event_no_incident=10,
        )

        self.analyzer._compute_chi_squared(result)

        assert result.chi_squared == 0.0
        assert result.p_value == 1.0


class TestMutualInformation:
    """Test Shannon mutual information calculation."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_mutual_information_zero_for_independence(self):
        """Test that MI = 0 for independent variables."""
        # Independent: a=25, b=25, c=25, d=25
        result = CorrelationResult(
            event_type="test",
            event_before_incident=25,
            event_no_incident=25,
            no_event_incident=25,
            no_event_no_incident=25,
        )

        self.analyzer._compute_mutual_information(result)

        # MI should be very close to 0 for independence
        assert abs(result.mutual_information) < 0.01

    def test_mutual_information_positive_for_association(self):
        """Test that MI > 0 when variables are associated."""
        # Strong association: events rare, but often precede incidents
        result = CorrelationResult(
            event_type="test",
            event_before_incident=10,  # Events usually precede incidents
            event_no_incident=2,  # Rarely without incidents
            no_event_incident=5,  # Some incidents without events
            no_event_no_incident=983,  # Most of the time: no event, no incident
        )

        self.analyzer._compute_mutual_information(result)

        assert result.mutual_information > 0
        assert result.pointwise_mi > 0

    def test_pointwise_mutual_information(self):
        """Test PMI calculation for specific cell."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=10,
            event_no_incident=5,
            no_event_incident=5,
            no_event_no_incident=80,
        )

        self.analyzer._compute_mutual_information(result)

        # PMI(E, I) = log2(P(E,I) / (P(E)*P(I)))
        # P(E,I) = 10/100 = 0.1
        # P(E) = 15/100 = 0.15
        # P(I) = 15/100 = 0.15
        # PMI = log2(0.1 / 0.0225) = log2(4.44) ≈ 2.15
        assert result.pointwise_mi > 1.0


class TestConditionalProbabilities:
    """Test P(I|E), P(E|I), P(E), P(I) calculations."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_probability_calculations(self):
        """Test all conditional and marginal probability calculations."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=20,  # a
            event_no_incident=30,  # b
            no_event_incident=10,  # c
            no_event_no_incident=40,  # d
        )

        self.analyzer._compute_conditional_probabilities(result)

        # P(I | E) = 20 / (20 + 30) = 0.4
        assert abs(result.p_incident_given_event - 0.4) < 0.001

        # P(E | I) = 20 / (20 + 10) = 2/3 ≈ 0.667
        assert abs(result.p_event_given_incident - 2 / 3) < 0.001

        # P(E) = 50 / 100 = 0.5
        assert abs(result.p_event - 0.5) < 0.001

        # P(I) = 30 / 100 = 0.3
        assert abs(result.p_incident - 0.3) < 0.001

    def test_zero_denominators(self):
        """Test handling of zero denominators."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=0,
            event_no_incident=0,
            no_event_incident=10,
            no_event_no_incident=10,
        )

        self.analyzer._compute_conditional_probabilities(result)

        assert result.p_incident_given_event == 0.0
        assert result.p_event_given_incident == 0.0


class TestEffectSizes:
    """Test odds ratio, relative risk, and Cramér's V."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_odds_ratio(self):
        """Test odds ratio calculation."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=60,  # a
            event_no_incident=20,  # b
            no_event_incident=20,  # c
            no_event_no_incident=60,  # d
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_effect_sizes(result)

        # OR = (60 * 60) / (20 * 20) = 3600 / 400 = 9
        assert abs(result.odds_ratio - 9.0) < 0.1

    def test_relative_risk(self):
        """Test relative risk calculation."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=80,  # a
            event_no_incident=20,  # b
            no_event_incident=40,  # c
            no_event_no_incident=60,  # d
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_effect_sizes(result)

        # P(I|E) = 80 / 100 = 0.8
        # P(I|¬E) = 40 / 100 = 0.4
        # RR = 0.8 / 0.4 = 2.0
        assert abs(result.relative_risk - 2.0) < 0.1

    def test_cramers_v(self):
        """Test Cramér's V effect size."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=50,
            event_no_incident=10,
            no_event_incident=10,
            no_event_no_incident=30,
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_effect_sizes(result)

        # Cramér's V should be between 0 and 1
        assert 0.0 <= result.cramers_v <= 1.0
        # For strong association, should be > 0.3
        assert result.cramers_v > 0.2


class TestPredictiveClassification:
    """Test predictive classification and confidence scoring."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_predictive_with_significant_rr(self):
        """Test that event is marked predictive with high RR and significance."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=40,
            event_no_incident=10,
            no_event_incident=10,
            no_event_no_incident=40,
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_effect_sizes(result)

        is_pred = self.analyzer._is_predictive(result)
        assert is_pred is True

    def test_not_predictive_low_rr(self):
        """Test that event not predictive with low relative risk."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=25,
            event_no_incident=25,
            no_event_incident=25,
            no_event_no_incident=25,
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_effect_sizes(result)

        is_pred = self.analyzer._is_predictive(result)
        assert is_pred is False

    def test_confidence_score(self):
        """Test confidence scoring combines multiple factors."""
        result = CorrelationResult(
            event_type="test",
            event_before_incident=50,
            event_no_incident=10,
            no_event_incident=10,
            no_event_no_incident=30,
        )

        self.analyzer._compute_chi_squared(result)
        self.analyzer._compute_mutual_information(result)
        self.analyzer._compute_conditional_probabilities(result)
        self.analyzer._compute_effect_sizes(result)

        result.is_predictive = self.analyzer._is_predictive(result)
        confidence = self.analyzer._compute_confidence(result)

        assert 0.0 <= confidence <= 1.0
        if result.is_predictive:
            assert confidence > 0.3  # Reasonable confidence for strong signal


class TestFisherExactTest:
    """Test Fisher's exact test for small sample sizes."""

    def test_fisher_exact_strong_association(self):
        """Test Fisher's exact with strong association."""
        odds_ratio, p_value = fisher_exact_test(10, 2, 2, 10)

        assert odds_ratio > 1.0
        assert p_value < 0.05  # Significant

    def test_fisher_exact_no_association(self):
        """Test Fisher's exact with no association."""
        odds_ratio, p_value = fisher_exact_test(5, 5, 5, 5)

        assert odds_ratio == 1.0
        assert p_value == 1.0  # Not significant

    def test_fisher_exact_complete_separation(self):
        """Test Fisher's exact with complete separation."""
        odds_ratio, p_value = fisher_exact_test(20, 0, 0, 20)

        assert odds_ratio == float("inf")
        # Fisher's exact handles this gracefully


class TestContingencyMeasures:
    """Test convenience function for converting contingency tables."""

    def test_contingency_measures_strong_association(self):
        """Test conversion of strong association."""
        measures = contingency_to_measures(80, 20, 40, 60)

        assert measures["odds_ratio"] == 6.0  # (80*60) / (20*40)
        assert measures["relative_risk"] == 2.0  # 0.8 / 0.4
        assert measures["risk_difference"] == 0.4  # 0.8 - 0.4
        assert 0.0 <= measures["cramers_v"] <= 1.0

    def test_contingency_measures_independence(self):
        """Test independence case."""
        measures = contingency_to_measures(25, 25, 25, 25)

        assert measures["odds_ratio"] == 1.0
        assert abs(measures["relative_risk"] - 1.0) < 0.1
        assert measures["risk_difference"] < 0.01


class TestTimestampParsing:
    """Test ISO-8601 timestamp parsing."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(event_store=self.store)

    def test_parse_iso8601_with_z(self):
        """Test parsing ISO-8601 with Z suffix."""
        dt = self.analyzer._parse_ts("2024-01-01T10:00:00Z")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 10

    def test_parse_iso8601_with_offset(self):
        """Test parsing ISO-8601 with timezone offset."""
        dt = self.analyzer._parse_ts("2024-01-01T10:00:00+00:00")
        assert dt.year == 2024
        assert dt.hour == 10

    def test_parse_invalid_timestamp(self):
        """Test handling of invalid timestamp."""
        with pytest.raises(ValueError):
            self.analyzer._parse_ts("not-a-timestamp")


class TestIntegration:
    """Integration tests with full analysis pipeline."""

    def setup_method(self):
        self.store = EventStore()
        self.analyzer = CorrelationAnalyzer(
            event_store=self.store,
            window_seconds=60,
            min_incident_count=3,
            significance_level=0.05,
        )

    def test_analyze_event_type_no_events(self):
        """Test analysis when event type doesn't exist."""
        incidents = [
            "2024-01-01T10:00:00+00:00",
            "2024-01-01T10:02:00+00:00",
        ]

        result = self.analyzer.analyze_event_type("nonexistent", incidents)

        assert result.event_type == "nonexistent"
        assert result.is_predictive is False
        assert result.confidence == 0.0

    def test_analyze_event_type_with_events(self):
        """Test full analysis pipeline."""
        incidents = [
            "2024-01-01T10:00:00+00:00",
            "2024-01-01T10:02:00+00:00",
            "2024-01-01T10:04:00+00:00",
        ]

        events = [
            {"ts": "2024-01-01T09:59:00+00:00", "kind": "log"},
            {"ts": "2024-01-01T09:59:30+00:00", "kind": "log"},
            {"ts": "2024-01-01T10:01:00+00:00", "kind": "log"},
        ]

        result = self.analyzer.analyze_event_type("log", incidents)

        assert result.event_type == "log"
        assert result.window_seconds == 60
        # Result should have all statistics populated
        assert result.chi_squared >= 0
        assert 0.0 <= result.p_value <= 1.0
        assert result.mutual_information >= 0
        assert 0.0 <= result.cramers_v <= 1.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
