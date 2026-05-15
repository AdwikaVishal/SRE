import json, sys
from adapters.engine import Engine
from schema import IncidentSignal

engine = Engine()

# Ingest the Annex A sample
events = []
sample = [
    '{"ts":"2026-05-10T14:21:30Z","kind":"deploy","service":"payments-svc","version":"v2.14.0","actor":"ci"}',
    '{"ts":"2026-05-10T14:22:01Z","kind":"log","service":"checkout-api","level":"error","msg":"timeout calling payments-svc","trace_id":"abc123"}',
    '{"ts":"2026-05-10T14:22:01Z","kind":"metric","service":"payments-svc","name":"latency_p99_ms","value":4820}',
    '{"ts":"2026-05-10T14:22:08Z","kind":"trace","trace_id":"abc123","spans":[{"svc":"checkout-api","dur_ms":5012},{"svc":"payments-svc","dur_ms":4980}]}',
    '{"ts":"2026-05-10T14:30:00Z","kind":"topology","change":"rename","from":"payments-svc","to":"billing-svc"}',
    '{"ts":"2026-05-10T14:32:11Z","kind":"incident_signal","incident_id":"INC-714","trigger":"alert:checkout-api/error-rate>5%"}',
    '{"ts":"2026-05-10T15:10:00Z","kind":"remediation","incident_id":"INC-714","action":"rollback","target":"billing-svc","version":"v2.13.4","outcome":"resolved"}'
]
for line in sample:
    events.append(json.loads(line))
engine.ingest(events)

# Reconstruct for INC-714 (post‑rename)
signal = IncidentSignal(
    incident_id="INC-714",
    service="billing-svc",   # renamed service
    ts="2026-05-10T14:32:11Z",
    trigger="alert:checkout-api/error-rate>5%"
)
ctx = engine.reconstruct_context(signal, mode="fast")

# Validate required fields
assert len(ctx["related_events"]) >= 4, "Missing related events"
assert len(ctx["causal_chain"]) >= 1, "Missing causal chain"
assert len(ctx["similar_past_incidents"]) >= 1, "Missing similar past incidents"
assert len(ctx["suggested_remediations"]) >= 1, "Missing suggested remediations"
assert ctx["confidence"] >= 0.5, "Confidence too low"
assert "rollback" in ctx["suggested_remediations"][0]["action"].lower(), "Remediation not rollback"

print("✅ Annex A test passed (rename robustness works)")
engine.close()
