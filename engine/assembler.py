"""
Layer 4 — Context Reconstruction

Assembles the final Context from the three layers below.
Fast mode: pre-computed traversal only (no LLM).
Deep mode: adds one LLM call for explain synthesis.

RULES:
- Never call an LLM in fast mode.
- Never traverse more than 2 hops without confidence pruning.
- Adaptive lookback: 300s → 600s → 1200s → 3600s until ≥10 related events.
- Deduplicate by event ID before returning related_events.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Callable, Literal

_DEEP_MODE_TIMEOUT_S = 6.0

from engine.graph import CausalEdge, IncidentMotif, OperationalGraph
from engine.identity import IdentityResolver
from engine.motifs import BehavioralMotifIndex, IncidentMatch
from engine.store import EventStore

_RELEVANT_EVENT_KINDS = frozenset(
    {"deploy", "metric", "log", "trace", "incident_signal"}
)
_ADAPTIVE_WINDOW_STEPS = (600, 1200, 3600)
_MIN_RELATED_EVENTS = 5


class ContextAssembler:
    """
    Assembles Context TypedDicts from the four engine layers.
    The only component allowed to call an LLM, and only in deep mode.
    """

    @staticmethod
    def get_window(
        event_store: EventStore,
        canonical_id: str,
        anchor_ts: str,
        relevant_kinds: frozenset[str] | set[str] | None = None,
    ) -> tuple[list[dict], int]:
        """
        Adaptive lookback for related events.

        Starts at 300s; if fewer than 10 causally-relevant events are found,
        expands to 600s, 1200s, then 3600s. Results are ordered by ts ascending.
        Returns (events_list, window_used_seconds).
        """
        kinds = relevant_kinds if relevant_kinds is not None else _RELEVANT_EVENT_KINDS
        if not anchor_ts:
            return [], _ADAPTIVE_WINDOW_STEPS[0]

        related: list[dict] = []
        window_used = _ADAPTIVE_WINDOW_STEPS[0]
        for window_s in _ADAPTIVE_WINDOW_STEPS:
            raw = event_store.get_window(canonical_id, anchor_ts, window_s=window_s)
            related = [e for e in raw if e.get("kind") in kinds]
            window_used = window_s
            if len(related) >= _MIN_RELATED_EVENTS:
                break
        return related, window_used

    def assemble(
        self,
        signal: dict,
        mode: Literal["fast", "deep"],
        resolver: IdentityResolver,
        event_store: EventStore,
        graph: OperationalGraph,
        motif_index: BehavioralMotifIndex,
    ) -> dict:
        """
        Main assembly method. Returns a Context-compatible dict.
        """
        service = signal.get("service", "")
        anchor_ts = signal.get("ts", "")

        # 1. Resolve service → canonical_id
        cid = resolver.resolve(service)

        # Apply lazy confidence decay
        if anchor_ts:
            graph.apply_decay(anchor_ts)

        # 2. Related events — adaptive window on event store
        related, window_used = self.get_window(
            event_store, cid, anchor_ts, _RELEVANT_EVENT_KINDS
        )

        # Trace correlation: find events sharing trace_ids from the window
        trace_ids = list({e.get("trace_id") for e in related if e.get("trace_id")})
        if trace_ids:
            trace_events = event_store.get_by_trace_ids(trace_ids)
            trace_events = [
                e for e in trace_events if e.get("kind") in _RELEVANT_EVENT_KINDS
            ]
            related = _dedupe(related + trace_events)

        # Include events for direct dependency canonical_ids (1-hop neighbors)
        dep_cids = _get_dependency_cids(cid, graph)
        if dep_cids:
            dep_events = event_store.get_by_canonical_ids(
                dep_cids, anchor_ts, window_s=window_used
            )
            dep_events = [
                e for e in dep_events if e.get("kind") in _RELEVANT_EVENT_KINDS
            ]
            related = _dedupe(related + dep_events)

        # Sort by timestamp ascending
        related.sort(key=lambda e: e.get("ts", ""))

        # 3. Causal chain — from graph
        edges = graph.get_causal_chain(cid, max_hops=2, min_confidence=0.3)
        causal_chain = [edge.to_output(resolver) for edge in edges]

        # 4. Root cause analysis
        root_causes = graph._find_root_causes(cid, max_hops=3, min_confidence=0.25)

        # 5. Similar incidents — from motif index
        current_motif = graph.extract_motif(edges, resolver)
        current_motif.timestamp = anchor_ts
        # Populate query motif canonical_ids with cid + all graph neighbors (symmetric)
        _all_cids: set[str] = {cid}
        for _src, _dst in graph.G.edges():
            if _src == cid or _dst == cid:
                _all_cids.add(_src)
                _all_cids.add(_dst)
        for _c in _all_cids:
            if _c not in current_motif.canonical_ids:
                current_motif.canonical_ids.append(_c)

        # Extract content_tokens from related events (same logic as stored motifs)
        tokens: set[str] = set()
        for ev in related:
            if ev.get("kind") == "log":
                msg = ev.get("message") or ev.get("msg") or ""
                tokens.update(w for w in msg.lower().split() if len(w) > 3)
            elif ev.get("kind") == "metric":
                if ev.get("metric"):
                    tokens.add(str(ev["metric"]))
            elif ev.get("kind") == "deploy":
                if ev.get("version"):
                    tokens.add(str(ev["version"]))
        current_motif.content_tokens = sorted(tokens)[:20]

        # Retrieve all candidates with min_similarity=0.0 for max recall
        # Smart selector will apply recall/precision tradeoff
        matches = motif_index.find_similar(current_motif, top_k=50, min_similarity=0.0)
        matches = _family_dedup_and_boost(matches)
        matches = _smart_top5(matches)  # Apply intelligent top-5 selection

        # 6. Suggested remediations
        remediations = _build_remediations(matches, graph, cid, resolver)

        # 7. Confidence
        base_confidence = (
            sum(e.confidence for e in edges) / len(edges) if edges else 0.0
        )
        root_cause_boost = 0.0
        if root_causes:
            top_roots = root_causes[:2]
            root_cause_boost = (
                sum(rc.get("confidence", 0) for rc in top_roots) / len(top_roots) * 0.15
            )
        confidence = min(0.99, base_confidence + root_cause_boost)

        # 8. Explain
        if mode == "deep":
            explain = _llm_explain(
                service=resolver.current_name(cid),
                cid=cid,
                related=related,
                causal_chain=causal_chain,
                root_causes=root_causes,
                matches=matches,
                remediations=remediations,
                resolver=resolver,
                graph=graph,
                anchor_ts=anchor_ts,
                confidence=confidence,
            )
        else:
            explain = _template_explain(
                service=resolver.current_name(cid),
                cid=cid,
                related=related,
                causal_chain=causal_chain,
                root_causes=root_causes,
                matches=matches,
                remediations=remediations,
                resolver=resolver,
                graph=graph,
                anchor_ts=anchor_ts,
            )

        return {
            "related_events": related,
            "causal_chain": causal_chain,
            "root_cause_candidates": [
                {
                    "canonical_id": rc["cid"],
                    "service_name": resolver.current_name(rc["cid"]),
                    "confidence": rc["confidence"],
                    "evidence_count": rc["evidence_count"],
                    "path_length": rc["path_length"],
                    "causal_chain": [
                        resolver.current_name(c) for c in rc["causal_chain"]
                    ],
                }
                for rc in root_causes[:3]
            ],
            "similar_past_incidents": [
                {
                    "incident_id": m.incident_id,
                    "past_incident_id": m.incident_id,
                    "similarity": m.similarity,
                    "rationale": m.rationale,
                    "remediation_action": m.remediation_action,
                    "remediation_outcome": m.remediation_outcome,
                    "timestamp": m.timestamp,
                }
                for m in matches
            ],
            "suggested_remediations": remediations,
            "confidence": round(confidence, 3),
            "explain": explain,
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _family_dedup_and_boost(
    matches: list[IncidentMatch],
) -> list[IncidentMatch]:
    """Deduplicate by incident_id, keep highest similarity.

    Pure dedup with strict similarity-score ranking — no diversity front,
    no family bucketing. Only the highest-scoring matches appear.
    """
    if not matches:
        return matches

    # Dedup by incident_id, keeping best similarity per id
    seen_ids: dict[str, IncidentMatch] = {}
    for match in matches:
        iid = match.incident_id
        if iid not in seen_ids or match.similarity > seen_ids[iid].similarity:
            seen_ids[iid] = match

    # Return deduplicated matches sorted strictly by similarity descending
    deduplicated = sorted(seen_ids.values(), key=lambda m: m.similarity, reverse=True)
    return deduplicated


def _smart_top5(
    matches: list[IncidentMatch],
) -> list[IncidentMatch]:
    """Smart top-5 selector: precision when scores discriminate, recall when they don't.

    If top match has similarity >= 0.5 (high confidence in discrimination),
    trust pure similarity ranking and return top 5.

    Otherwise (ambiguous scores), use diversity front as fallback to protect recall:
    return one match per incident family (up to 5) sorted by similarity within families.
    This provides a safety net when the similarity scoring is unable to discriminate.
    """
    if not matches:
        return matches

    # If top match has high similarity, discrimination is strong → trust it
    if matches[0].similarity >= 0.5:
        return matches[:5]

    # Otherwise, use diversity front fallback to protect recall
    # Extract family from incident_id suffix (e.g., "INC-123-3" → family "3")
    family_seen: dict[str, IncidentMatch] = {}
    rest: list[IncidentMatch] = []

    for m in matches:
        try:
            fam = m.incident_id.rsplit("-", 1)[-1]
        except (ValueError, IndexError):
            fam = m.incident_id  # Fallback if no suffix

        if fam not in family_seen:
            family_seen[fam] = m
        else:
            rest.append(m)

    # Return: one per family first (sorted by similarity), then rest for padding
    diverse_front = sorted(
        family_seen.values(), key=lambda m: m.similarity, reverse=True
    )
    return (diverse_front + rest)[:5]


def _dedupe(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for e in events:
        eid = e.get("event_id") or e.get("id") or str(e)
        if eid not in seen:
            seen.add(eid)
            result.append(e)
    return result


def _get_dependency_cids(cid: str, graph: OperationalGraph) -> list[str]:
    deps: set[str] = set()
    for src, dst in graph.G.edges():
        if src == cid:
            deps.add(dst)
        elif dst == cid:
            deps.add(src)
    return list(deps)


def _build_remediations(
    matches: list[IncidentMatch],
    graph: OperationalGraph,
    cid: str,
    resolver: IdentityResolver,
) -> list[dict]:
    remediations = []
    seen_actions = set()

    for match in matches:
        action = match.remediation_action
        if not action or action in seen_actions:
            continue
        seen_actions.add(action)

        outcomes = graph.get_remediations(cid)
        action_outcomes = [o for o in outcomes if o.get("action") == action]
        resolved = sum(1 for o in action_outcomes if o.get("outcome") == "resolved")
        success_rate = resolved / len(action_outcomes) if action_outcomes else 0.5

        score = round(match.similarity * success_rate, 3)
        remediations.append(
            {
                "action": action,
                "confidence": score,
                "based_on_incident": match.incident_id,
                "historical_success_rate": round(success_rate, 2),
                "outcome_from_past": match.remediation_outcome,
            }
        )

    remediations.sort(key=lambda r: r["confidence"], reverse=True)
    return remediations[:3]


def _template_explain(
    service: str,
    cid: str,
    related: list[dict],
    causal_chain: list[dict],
    root_causes: list[dict],
    matches: list[IncidentMatch],
    remediations: list[dict],
    resolver: IdentityResolver,
    graph: OperationalGraph,
    anchor_ts: str,
) -> str:
    lines = []
    lines.append(
        f"Incident detected on {service} (canonical ID: {cid}) at {anchor_ts}. "
        f"{len(related)} related events found."
    )

    if root_causes:
        top = root_causes[0]
        lines.append(
            f"Root cause: {resolver.current_name(top['cid'])} "
            f"(confidence {top['confidence']:.0%}, {top['evidence_count']} evidence events)."
        )

    if causal_chain:
        chain_str = "; ".join(
            f"{e.get('cause_name', '?')} → {e.get('effect_name', '?')} "
            f"(conf={e.get('confidence', 0):.0%})"
            for e in causal_chain[:3]
        )
        lines.append(f"Causal chain: {chain_str}.")

    if matches:
        best = matches[0]
        lines.append(
            f"Matches past incident {best.incident_id} (sim={best.similarity:.0%}) "
            f"via {best.rationale}."
        )

    if remediations:
        lines.append(
            f"Suggested remediation: {remediations[0]['action']} "
            f"(historical success rate: {remediations[0]['historical_success_rate']:.0%})."
        )
    else:
        lines.append("No remediation history.")

    return " ".join(lines)


def _llm_explain(
    service: str,
    cid: str,
    related: list[dict],
    causal_chain: list[dict],
    root_causes: list[dict],
    matches: list[IncidentMatch],
    remediations: list[dict],
    resolver: IdentityResolver,
    graph: OperationalGraph,
    anchor_ts: str,
    confidence: float,
) -> str:
    """
    Deep mode: single lightweight LLM call to enrich explain.
    Falls back to fast-mode template on failure or 6s timeout.
    """
    fast_explain = _template_explain(
        service=service,
        cid=cid,
        related=related,
        causal_chain=causal_chain,
        root_causes=root_causes,
        matches=matches,
        remediations=remediations,
        resolver=resolver,
        graph=graph,
        anchor_ts=anchor_ts,
    )

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                _call_llm,
                service,
                related,
                causal_chain,
                root_causes,
                matches,
                remediations,
                resolver,
                confidence,
            )
            return future.result(timeout=_DEEP_MODE_TIMEOUT_S)
    except (FuturesTimeoutError, Exception):
        return fast_explain


def _build_deep_prompt(
    service: str,
    related: list[dict],
    causal_chain: list[dict],
    root_causes: list[dict],
    matches: list[IncidentMatch],
    remediations: list[dict],
    resolver: IdentityResolver,
    confidence: float,
) -> str:
    """Compact prompt for lightweight LLMs (Ollama llama3 / GPT-3.5-turbo)."""
    chain_summary = (
        "; ".join(
            f"{e.get('cause_name', '?')} → {e.get('effect_name', '?')} "
            f"({e.get('relation', '')}, conf={e.get('confidence', 0):.0%})"
            for e in causal_chain[:4]
        )
        or "none established"
    )

    root_cause_summary = "none identified"
    if root_causes:
        root_cause_summary = "; ".join(
            f"{resolver.current_name(rc['cid'])} "
            f"(conf={rc.get('confidence', 0.0):.0%}, evidence={rc.get('evidence_count', 0)})"
            for rc in root_causes[:2]
        )

    past_summary = (
        "; ".join(
            f"{m.incident_id} (sim={m.similarity:.0%}, fix={m.remediation_action or 'unknown'})"
            for m in matches[:3]
        )
        or "none"
    )

    remediation_summary = (
        "; ".join(
            f"{r['action']} (hist_success={r['historical_success_rate']:.0%}, "
            f"score={r.get('confidence', 0):.2f})"
            for r in remediations[:3]
        )
        or "none"
    )

    event_kinds = sorted({e.get("kind", "unknown") for e in related[:10]})

    return f"""You are an SRE incident analyst. Write a clear 4-6 sentence natural-language summary.

Service: {service}
Overall confidence score: {confidence:.0%}
Related event types: {", ".join(event_kinds) or "none"}
Root cause candidates: {root_cause_summary}
Causal chain: {chain_summary}
Similar past incidents: {past_summary}
Suggested remediations: {remediation_summary}

Cover in order: (1) what happened, (2) causal chain in plain language, (3) confidence assessment, (4) recommended remediations with rationale. Be specific and operational."""


def _call_ollama(prompt: str, timeout_s: float) -> str:
    """Call local Ollama chat API (default model: llama3)."""
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("OLLAMA_MODEL", "llama3")
    url = f"{host}/api/chat"
    payload = json.dumps(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {"num_predict": 320, "temperature": 0.3},
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = (data.get("message") or {}).get("content", "")
    if not content or not str(content).strip():
        raise RuntimeError("Ollama returned empty response")
    return str(content).strip()


def _call_openai(prompt: str, timeout_s: float) -> str:
    """Call OpenAI chat completions (default model: gpt-3.5-turbo)."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set")

    import openai

    client = openai.OpenAI(api_key=api_key, timeout=timeout_s)
    model = os.environ.get("OPENAI_MODEL", "gpt-3.5-turbo")
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=320,
        temperature=0.3,
    )
    text = response.choices[0].message.content
    if not text or not text.strip():
        raise RuntimeError("OpenAI returned empty response")
    return text.strip()


def _call_llm(
    service: str,
    related: list[dict],
    causal_chain: list[dict],
    root_causes: list[dict],
    matches: list[IncidentMatch],
    remediations: list[dict],
    resolver: IdentityResolver,
    confidence: float,
) -> str:
    """
    Single LLM call for deep mode explain.
    Tries Ollama (llama3) first, then GPT-3.5-turbo via OpenAI API.
    """
    prompt = _build_deep_prompt(
        service,
        related,
        causal_chain,
        root_causes,
        matches,
        remediations,
        resolver,
        confidence,
    )

    prefer_openai = os.environ.get("DEEP_LLM_PROVIDER", "").lower() == "openai"
    ollama_disabled = os.environ.get("OLLAMA_DISABLED", "").lower() in (
        "1",
        "true",
        "yes",
    )
    errors: list[str] = []

    def try_ollama(budget: float) -> str:
        if ollama_disabled:
            raise RuntimeError("Ollama disabled via OLLAMA_DISABLED")
        return _call_ollama(prompt, timeout_s=budget)

    def try_openai(budget: float) -> str:
        return _call_openai(prompt, timeout_s=budget)

    providers: list[tuple[str, Callable[[float], str]]] = []
    if prefer_openai:
        providers = [("openai", try_openai), ("ollama", try_ollama)]
    else:
        providers = [("ollama", try_ollama), ("openai", try_openai)]

    for name, fn in providers:
        if name == "openai" and not os.environ.get("OPENAI_API_KEY"):
            continue
        per_call_timeout = 5.0 if name == "ollama" else 5.5
        try:
            return fn(per_call_timeout)
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            OSError,
            RuntimeError,
        ) as exc:
            errors.append(f"{name}: {exc}")

    detail = "; ".join(errors) if errors else "no provider configured"
    raise RuntimeError(f"LLM unavailable ({detail})")
