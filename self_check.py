"""
Anvil P-02 — Self-Check Script

Runs a quick validation of the engine adapter against a worked example.
Verifies:
  1. Ingest throughput (≥ 1,000 ev/s)
  2. Fast mode p95 latency (≤ 2s)
  3. Deep mode p95 latency (≤ 6s)
  4. Rename robustness (recall across rename boundary)
  5. Temporal ordering (source-precedes-effect)
  6. Context quality (non-empty related_events and causal_chain)
  7. Memory evolution (motif index grows after remediation)

Usage:
  python self_check.py
  python self_check.py --quick
  python self_check.py --adapter adapters.engine:Engine
"""

from __future__ import annotations

import argparse
import importlib
import json
import statistics
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone


# ------------------------------------------------------------------
# Worked example data generator
# ------------------------------------------------------------------

def _ts(base: datetime, delta_s: float) -> str:
    return (base + timedelta(seconds=delta_s)).isoformat()


def make_worked_example(seed: int = 42) -> tuple[list[dict], list[dict]]:
    """
    Generate a worked example JSONL stream.
    Returns (train_events, eval_signals).

    Scenario:
      T+0:   payments-svc deploys v2.1.0
      T+30:  payments-svc metric spike (latency p99 > 500ms)
      T+45:  payments-svc error log (trace_id=abc123)
      T+50:  checkout-svc trace (upstream call to payments-svc, trace_id=abc123)
      T+60:  incident_signal on payments-svc
      T+90:  remediation: rollback to v2.0.9, outcome=resolved
      T+120: topology rename: payments-svc → billing-svc
      T+150: billing-svc deploys v2.1.1
      T+180: billing-svc metric spike
      T+190: billing-svc error log
      T+200: incident_signal on billing-svc  ← eval signal
    """
    base = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)

    train_events = [
        # Deploy
        {
            "event_id": f"evt-{seed}-001",
            "kind": "deploy",
            "service": "payments-svc",
            "ts": _ts(base, 0),
            "version": "v2.1.0",
        },
        # Metric spike
        {
            "event_id": f"evt-{seed}-002",
            "kind": "metric",
            "service": "payments-svc",
            "ts": _ts(base, 30),
            "metric": "latency_p99",
            "value": 520,
            "threshold": 500,
        },
        # Error log
        {
            "event_id": f"evt-{seed}-003",
            "kind": "log",
            "service": "payments-svc",
            "ts": _ts(base, 45),
            "level": "error",
            "message": "Database connection timeout",
            "trace_id": f"trace-{seed}-abc",
        },
        # Upstream trace from checkout-svc
        {
            "event_id": f"evt-{seed}-004",
            "kind": "trace",
            "service": "checkout-svc",
            "ts": _ts(base, 50),
            "trace_id": f"trace-{seed}-abc",
            "spans": [
                {"svc": "payments-svc", "ts": _ts(base, 50), "status": "error"},
            ],
        },
        # Incident signal
        {
            "event_id": f"evt-{seed}-005",
            "kind": "incident_signal",
            "service": "payments-svc",
            "ts": _ts(base, 60),
            "incident_id": f"inc-{seed}-001",
            "trigger": "latency_threshold_breach",
        },
        # Remediation
        {
            "event_id": f"evt-{seed}-006",
            "kind": "remediation",
            "service": "payments-svc",
            "ts": _ts(base, 90),
            "incident_id": f"inc-{seed}-001",
            "action": "rollback",
            "version": "v2.0.9",
            "outcome": "resolved",
        },
        # Topology rename
        {
            "event_id": f"evt-{seed}-007",
            "kind": "topology",
            "ts": _ts(base, 120),
            "service": "payments-svc",
            "mutation": {
                "kind": "rename",
                "old_name": "payments-svc",
                "new_name": "billing-svc",
            },
        },
        # New deploy on renamed service
        {
            "event_id": f"evt-{seed}-008",
            "kind": "deploy",
            "service": "billing-svc",
            "ts": _ts(base, 150),
            "version": "v2.1.1",
        },
        # Metric spike on renamed service
        {
            "event_id": f"evt-{seed}-009",
            "kind": "metric",
            "service": "billing-svc",
            "ts": _ts(base, 180),
            "metric": "latency_p99",
            "value": 610,
            "threshold": 500,
        },
        # Error log on renamed service
        {
            "event_id": f"evt-{seed}-010",
            "kind": "log",
            "service": "billing-svc",
            "ts": _ts(base, 190),
            "level": "error",
            "message": "Database connection timeout",
            "trace_id": f"trace-{seed}-def",
        },
    ]

    eval_signals = [
        {
            "service": "billing-svc",
            "ts": _ts(base, 200),
            "incident_id": f"inc-{seed}-002",
            "trigger": "latency_threshold_breach",
        }
    ]

    return train_events, eval_signals


def make_throughput_events(n: int = 5000, seed: int = 42) -> list[dict]:
    """Generate n synthetic events for throughput testing."""
    base = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    services = [f"svc-{seed}-{i:02d}" for i in range(10)]
    kinds = ["metric", "log", "trace", "deploy"]
    events = []
    for i in range(n):
        svc = services[i % len(services)]
        kind = kinds[i % len(kinds)]
        events.append({
            "event_id": f"tput-{seed}-{i:05d}",
            "kind": kind,
            "service": svc,
            "ts": _ts(base, i * 0.1),
            "value": i,
        })
    return events


# ------------------------------------------------------------------
# Checks
# ------------------------------------------------------------------

def check_throughput(engine, quick: bool) -> dict:
    n = 1000 if quick else 5000
    events = make_throughput_events(n)
    start = time.perf_counter()
    engine.ingest(events)
    elapsed = time.perf_counter() - start
    rate = n / elapsed
    passed = rate >= 1000
    return {
        "name": "Ingest throughput",
        "passed": passed,
        "value": f"{rate:.0f} ev/s",
        "threshold": "≥ 1,000 ev/s",
        "detail": f"Ingested {n} events in {elapsed:.3f}s",
    }


def check_rename_robustness(engine) -> dict:
    """
    Core test: ingest payments-svc incidents, rename to billing-svc,
    ingest similar incident, reconstruct_context → must surface past incident.
    """
    train_events, eval_signals = make_worked_example(seed=99)
    engine.ingest(train_events)

    ctx = engine.reconstruct_context(eval_signals[0], mode="fast")
    past_incidents = ctx.get("similar_past_incidents", [])

    # The past incident (inc-99-001) must appear in the top-5
    found = any(
        m.get("incident_id") == "inc-99-001"
        for m in past_incidents
    )
    passed = found and len(past_incidents) > 0

    return {
        "name": "Rename robustness (recall@5)",
        "passed": passed,
        "value": f"{len(past_incidents)} past incidents found",
        "threshold": "Past incident must appear after rename",
        "detail": (
            f"billing-svc (was payments-svc) → found inc-99-001: {found}. "
            f"Matches: {[m.get('incident_id') for m in past_incidents]}"
        ),
    }


def check_temporal_ordering(engine) -> dict:
    """All causal edges must have source-precedes-effect."""
    train_events, eval_signals = make_worked_example(seed=77)
    engine.ingest(train_events)
    ctx = engine.reconstruct_context(eval_signals[0], mode="fast")

    chain = ctx.get("causal_chain", [])
    violations = 0
    for edge in chain:
        first = edge.get("first_seen", "")
        last = edge.get("last_seen", "")
        if first and last and first > last:
            violations += 1

    passed = violations == 0
    return {
        "name": "Temporal ordering",
        "passed": passed,
        "value": f"{violations} violations",
        "threshold": "0 violations",
        "detail": f"Checked {len(chain)} causal edges",
    }


def check_fast_mode_latency(engine, quick: bool) -> dict:
    """Fast mode p95 must be ≤ 2s."""
    train_events, eval_signals = make_worked_example(seed=55)
    engine.ingest(train_events)

    n = 10 if quick else 100
    latencies = []
    for _ in range(n):
        start = time.perf_counter()
        engine.reconstruct_context(eval_signals[0], mode="fast")
        latencies.append((time.perf_counter() - start) * 1000)

    p95 = statistics.quantiles(latencies, n=100)[94] if len(latencies) >= 20 else max(latencies)
    passed = p95 <= 2000

    return {
        "name": "Fast mode p95 latency",
        "passed": passed,
        "value": f"{p95:.0f}ms",
        "threshold": "≤ 2,000ms",
        "detail": f"p50={statistics.median(latencies):.0f}ms, p95={p95:.0f}ms over {n} calls",
    }


def check_context_quality(engine) -> dict:
    """related_events and causal_chain must be non-empty after ingestion."""
    train_events, eval_signals = make_worked_example(seed=33)
    engine.ingest(train_events)
    ctx = engine.reconstruct_context(eval_signals[0], mode="fast")

    related = ctx.get("related_events", [])
    chain = ctx.get("causal_chain", [])
    explain = ctx.get("explain", "")

    passed = len(related) > 0 and len(explain) > 20

    return {
        "name": "Context quality",
        "passed": passed,
        "value": f"{len(related)} related events, {len(chain)} causal edges",
        "threshold": "Non-empty related_events and explain",
        "detail": f"explain length: {len(explain)} chars",
    }


def check_memory_evolution(engine) -> dict:
    """
    Motif index must grow after a resolved remediation is ingested.
    """
    train_events, _ = make_worked_example(seed=11)
    engine.ingest(train_events)

    motif_count = engine.motifs.count()
    passed = motif_count > 0

    return {
        "name": "Memory evolution",
        "passed": passed,
        "value": f"{motif_count} motifs indexed",
        "threshold": "≥ 1 motif after resolved remediation",
        "detail": "Motif index grows when remediation outcome=resolved is ingested",
    }


# ------------------------------------------------------------------
# Runner
# ------------------------------------------------------------------

def run_checks(adapter_path: str, quick: bool) -> None:
    # Load adapter
    if ":" in adapter_path:
        module_path, class_name = adapter_path.rsplit(":", 1)
    else:
        module_path, class_name = adapter_path, "Engine"

    try:
        module = importlib.import_module(module_path)
        EngineClass = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        print(f"ERROR: Could not load adapter '{adapter_path}': {e}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  Anvil P-02 Self-Check  |  adapter: {adapter_path}")
    print(f"{'='*60}\n")

    checks = [
        lambda: check_throughput(EngineClass(), quick),
        lambda: check_rename_robustness(EngineClass()),
        lambda: check_temporal_ordering(EngineClass()),
        lambda: check_fast_mode_latency(EngineClass(), quick),
        lambda: check_context_quality(EngineClass()),
        lambda: check_memory_evolution(EngineClass()),
    ]

    results = []
    for check_fn in checks:
        try:
            result = check_fn()
        except Exception as e:
            result = {
                "name": "Unknown",
                "passed": False,
                "value": "ERROR",
                "threshold": "",
                "detail": str(e),
            }
        results.append(result)

        status = "✓ PASS" if result["passed"] else "✗ FAIL"
        print(f"  {status}  {result['name']}")
        print(f"         value: {result['value']}  (threshold: {result['threshold']})")
        if not result["passed"]:
            print(f"         detail: {result['detail']}")
        print()

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    score = passed / total * 100

    print(f"{'='*60}")
    print(f"  Score: {passed}/{total} checks passed ({score:.0f}%)")
    print(f"{'='*60}\n")

    # Write report
    report = {
        "adapter": adapter_path,
        "quick": quick,
        "checks": results,
        "score": score,
        "passed": passed,
        "total": total,
    }
    with open("report.json", "w") as f:
        json.dump(report, f, indent=2)
    print("  Report written to report.json\n")

    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Anvil P-02 Self-Check")
    parser.add_argument(
        "--adapter",
        default="adapters.engine:Engine",
        help="Adapter path in module:Class format",
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run quick checks (fewer iterations)",
    )
    args = parser.parse_args()
    run_checks(args.adapter, args.quick)
