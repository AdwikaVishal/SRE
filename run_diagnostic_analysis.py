#!/usr/bin/env python3
"""
run_diagnostic_analysis.py — Quick diagnostic analysis runner.

Usage:
    python run_diagnostic_analysis.py --diagnostic-file diagnostics_all.json --output analysis_results

This script:
1. Loads diagnostic JSON from benchmark run
2. Runs failure analysis
3. Runs score attribution analysis
4. Produces CSV and JSON reports
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from benchmark_score_attribution import ScoreAttributionAnalyzer
from failure_analysis import FailureAnalyzer


def main():
    ap = argparse.ArgumentParser(
        description="Run comprehensive diagnostic analysis on benchmark results"
    )
    ap.add_argument(
        "--diagnostic-file",
        required=True,
        help="Path to diagnostics JSON file from BenchmarkDiagnosticsCollector",
    )
    ap.add_argument(
        "--output",
        default="analysis_results",
        help="Output directory for analysis files (will create if needed)",
    )
    ap.add_argument(
        "--verbose",
        action="store_true",
        help="Print summary statistics to console",
    )
    args = ap.parse_args()

    # Create output directory
    out_dir = Path(args.output)
    out_dir.mkdir(exist_ok=True, parents=True)

    print()
    print("=" * 70)
    print("  DIAGNOSTIC ANALYSIS FRAMEWORK")
    print("=" * 70)
    print()

    # ========== FAILURE ANALYSIS ==========
    print("📊 Running failure analysis...")
    failure_analyzer = FailureAnalyzer(args.diagnostic_file)

    failure_json_path = out_dir / "failure_analysis.json"
    failure_analyzer.export_json(str(failure_json_path))
    print(f"   ✓ Exported: {failure_json_path}")

    # ========== SCORE ATTRIBUTION ==========
    print()
    print("🎯 Running score attribution analysis...")
    score_analyzer = ScoreAttributionAnalyzer(args.diagnostic_file)

    attribution_json_path = out_dir / "score_attribution.json"
    score_analyzer.export_json(str(attribution_json_path))
    print(f"   ✓ Exported: {attribution_json_path}")

    # ========== SUMMARY STATISTICS ==========
    if args.verbose:
        print()
        print("=" * 70)
        print("  SUMMARY STATISTICS")
        print("=" * 70)

        # Failure analysis summary
        print()
        print("📈 Precision by Family:")
        precision_by_fam = failure_analyzer.precision_decay_by_family()
        for fam in sorted(precision_by_fam.keys()):
            stats = precision_by_fam[fam]
            print(
                f"  Family {fam:3}: "
                f"n={stats['num_incidents']:2d} "
                f"recall={stats['recall']:.2f} "
                f"precision@5={stats['precision@5']:.3f} "
                f"rank1={stats['rank1_accuracy']:.2f}"
            )

        # Top substitutions
        print()
        print("🔄 Top Family Substitutions (Wrong Matches):")
        subs = failure_analyzer.family_substitution_stats(top_n=5)
        for sub in subs:
            print(
                f"  True Family {sub['true_family']} → "
                f"Wrong Family {sub['wrong_family']}: {sub['count']} times"
            )

        # Remediation analysis
        print()
        print("💊 Remediation Analysis:")
        remed = failure_analyzer.remediation_mismatches()
        if remed["total_with_expected_remediation"] > 0:
            match_rate = (
                remed["exact_matches"] / remed["total_with_expected_remediation"]
            )
            print(
                f"  Match rate: {remed['exact_matches']} / "
                f"{remed['total_with_expected_remediation']} = {match_rate:.2f}"
            )
            print(f"  Missing actions: {remed['missing_action']}")
            print(f"  Wrong actions: {remed['wrong_action']}")
        else:
            print("  (No expected remediations)")

        # Decoy analysis
        print()
        print("🚫 Decoy Analysis:")
        decoy = failure_analyzer.decoy_failure_analysis()
        print(f"  Total decoys: {decoy['total_decoys']}")
        print(f"  Correctly rejected: {decoy['correctly_rejected']}")
        print(f"  False positives: {decoy['false_positives']}")
        print(f"  Decoy recall: {decoy['decoy_recall']:.2f}")
        print(f"  False positive rate: {decoy['false_positive_rate']:.2f}")

        # Score attribution summary
        print()
        print("⚠️  Score Loss Breakdown:")
        loss_breakdown = score_analyzer.aggregate_loss_breakdown()
        for category, stats in loss_breakdown["per_category"].items():
            print(
                f"  {category}: {stats['count']} incidents, "
                f"{stats['total_loss_points']:.1f} loss points"
            )
        print(
            f"  TOTAL LOSS: {loss_breakdown['total_loss_points']:.1f} / "
            f"{loss_breakdown['total_incidents']} incidents"
        )

        # Top failures
        print()
        print("🔴 Highest-Impact Failures (top 5):")
        failures = score_analyzer.highest_impact_failures(top_n=5)
        for i, failure in enumerate(failures, 1):
            print(f"  {i}. {failure['incident_id']} — {failure['loss_category']}")

        # Confidence calibration
        print()
        print("🎚️  Confidence Calibration:")
        calib = score_analyzer.confidence_calibration_analysis()
        print(f"  High-confidence but wrong: {calib['high_confidence_but_wrong']}")
        print(f"  Low-confidence but correct: {calib['low_confidence_but_correct']}")
        print(
            f"  Mean confidence when correct: "
            f"{calib['mean_confidence_when_correct']:.3f}"
        )
        print(
            f"  Mean confidence when wrong: {calib['mean_confidence_when_wrong']:.3f}"
        )
        print(f"  Confidence separation: {calib['confidence_separation']:.3f}")

    print()
    print("=" * 70)
    print("  ANALYSIS COMPLETE")
    print("=" * 70)
    print()
    print(f"Results saved to: {out_dir.absolute()}")
    print()
    print("📄 Generated files:")
    print(f"   - {failure_json_path.name}")
    print(f"   - {attribution_json_path.name}")
    print()


if __name__ == "__main__":
    main()
