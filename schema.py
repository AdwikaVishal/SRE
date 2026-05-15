"""
Anvil P-02 Schema Definitions

Defines the data structures used by the engine adapter.
These TypedDicts match the harness's expected interface.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Event(TypedDict, total=False):
    """An infrastructure event."""
    event_id: str
    kind: str           # deploy | log | metric | trace | topology | incident_signal | remediation
    service: str        # raw service name (resolver maps to canonical_id)
    ts: str             # ISO-8601 timestamp
    trace_id: str       # optional trace correlation ID
    version: str        # for deploy events
    level: str          # for log events: info | warn | error | critical
    spans: list[dict]   # for trace events
    mutation: dict      # for topology events
    incident_id: str    # for incident_signal and remediation events
    trigger: str        # for incident_signal events
    action: str         # for remediation events
    outcome: str        # for remediation events: resolved | escalated | partial


class CausalEdge(TypedDict):
    """A directed causal relationship between two entities."""
    cause_id: str       # canonical_id of the cause
    effect_id: str      # canonical_id of the effect
    cause_name: str     # current display name of cause
    effect_name: str    # current display name of effect
    relation: str       # type of causal relationship
    confidence: float   # 0.0–1.0
    first_seen: str
    last_seen: str


class SimilarIncident(TypedDict):
    """A past incident that matches the current pattern."""
    incident_id: str
    similarity: float   # 0.0–1.0
    rationale: str      # human-readable explanation of why it matches
    remediation_action: str
    remediation_outcome: str
    timestamp: str


class Remediation(TypedDict):
    """A suggested remediation action."""
    action: str
    confidence: float
    based_on_incident: str
    historical_success_rate: float
    outcome_from_past: str


class IncidentSignal(TypedDict, total=False):
    """Signal that triggers context reconstruction."""
    service: str        # raw service name
    ts: str             # ISO-8601 timestamp
    incident_id: str
    trigger: str


class Context(TypedDict):
    """The assembled context returned by reconstruct_context()."""
    related_events: list[dict]
    causal_chain: list[CausalEdge]
    similar_past_incidents: list[SimilarIncident]
    suggested_remediations: list[Remediation]
    confidence: float
    explain: str
