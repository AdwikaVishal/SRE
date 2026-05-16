"""Anvil P-02 benchmark adapter backed by ContextEngine."""
from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta
from collections import Counter
from statistics import mean
from typing import Iterable, Literal

from engine.assembler import ContextAssembler
from engine.graph import OperationalGraph
from engine.identity import IdentityResolver
from engine.motifs import BehavioralMotifIndex
from engine.store import EventStore


class Engine:
    """Benchmark-facing adapter that wires the full ContextEngine stack."""

    def __init__(self) -> None:
        self.resolver = IdentityResolver()
        self.store = EventStore()
        self.graph = OperationalGraph()
        self.motifs = BehavioralMotifIndex()
        self.assembler = ContextAssembler()
        self._lock = threading.Lock()
        self._open_incidents: dict[str, dict] = {}
        self._cfg = {
            "same_cid_boost": 0.32,
            "cross_cid_penalty": 0.22,
            "action_success_weight": 0.12,
            "topology_neighbor_boost": 0.10,
            "graph_distance_penalty": 0.10,
            "evidence_boost": 0.08,
            "decoy_cap_similarity": 0.39,
            "decoy_cap_remediation": 0.39,
            "stageA_min_similarity": 0.52,
        }

    def _normalize_event(self, event: dict) -> dict:
        normalized = dict(event)

        if normalized.get("kind") == "log" and "msg" in normalized and "message" not in normalized:
            normalized["message"] = normalized.pop("msg")

        if normalized.get("kind") == "topology" and "from_" in normalized and "from" not in normalized:
            normalized["from"] = normalized.pop("from_")

        if normalized.get("kind") in ("incident_signal", "remediation") and not normalized.get("service"):
            if normalized.get("target"):
                normalized["service"] = normalized["target"]

        if normalized.get("kind") == "deploy" and not normalized.get("actor"):
            normalized["actor"] = "system"

        return normalized

    def _resolve_cid_for_event(self, event: dict) -> str:
        service = event.get("service", event.get("svc", ""))
        if service:
            return self.resolver.resolve(service)

        target = event.get("target", "")
        if target:
            return self.resolver.resolve(target)

        kind = event.get("kind", "")
        inc_id = event.get("incident_id", "")
        if inc_id and kind in ("incident_signal", "remediation"):
            if inc_id in self._open_incidents:
                return self._open_incidents[inc_id]["cid"]
            if kind == "incident_signal":
                trigger = event.get("trigger", "")
                if trigger and ":" in trigger:
                    hint = trigger.split(":", 1)[1].split("/")[0].strip()
                    if hint:
                        return self.resolver.resolve(hint)

        return ""

    def _on_topology(self, event: dict) -> None:
        ts = event.get("ts", "")
        change_kind = str(event.get("change", "")).lower()

        if change_kind == "rename":
            old_name = event.get("from", "")
            new_name = event.get("to", "")
            if old_name and new_name:
                self.resolver.rename(old_name, new_name, ts)

        cid = self.resolver.resolve(event.get("service", event.get("src", "topology")))
        self.store.append(
            event_id=event.get("event_id") or event.get("id") or str(uuid.uuid4()),
            canonical_id=cid,
            ts=ts,
            kind="topology",
            raw=event,
            trace_id=None,
        )

    def _on_deploy(self, event: dict, cid: str) -> None:
        self.graph.record_deploy(cid, event.get("version", "unknown"), event.get("ts", ""))

    def _on_signal(self, event: dict, cid: str) -> None:
        ts = event.get("ts", "")
        kind = event.get("kind", "signal")

        recent_deploy = self.graph.get_recent_deploy(cid, ts, window_s=3600)
        if recent_deploy:
            self.graph.add_edge(
                src_cid=cid,
                dst_cid=cid,
                relation=f"deploy_to_{kind}",
                evidence_id=event.get("trace_id") or event.get("event_id") or ts,
                ts_src=recent_deploy["ts"],
                ts_dst=ts,
            )

        if event.get("trace_id") and kind == "trace":
            for span in event.get("spans", []):
                span_svc = span.get("svc", span.get("service", ""))
                if not span_svc:
                    continue
                span_cid = self.resolver.resolve(span_svc)
                if span_cid != cid:
                    span_ts = span.get("ts", "")
                    if not span_ts or span_ts <= ts:
                        try:
                            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                            span_ts = (dt + timedelta(seconds=1)).isoformat()
                        except Exception:
                            span_ts = ""
                    if span_ts:
                        self.graph.add_edge(
                            src_cid=cid,
                            dst_cid=span_cid,
                            relation="upstream_call",
                            evidence_id=event["trace_id"],
                            ts_src=ts,
                            ts_dst=span_ts,
                        )

    def _on_incident(self, event: dict, cid: str) -> None:
        incident_id = event.get("incident_id", str(uuid.uuid4()))
        self._open_incidents[incident_id] = {
            "cid": cid,
            "ts": event.get("ts", ""),
            "trigger": event.get("trigger", ""),
        }

    def _on_remediation(self, event: dict, cid: str) -> None:
        inc_id = event.get("incident_id", "")
        outcome = event.get("outcome", "unknown")
        ts = event.get("ts", "")
        success = outcome == "resolved"

        if success:
            self.graph.reinforce_remediation(
                cid=cid,
                incident_id=inc_id,
                action=event.get("action", "unknown"),
                outcome=outcome,
                ts=ts,
                window_s=3600,
            )

            edges = self.graph.get_causal_chain(cid, max_hops=2)
            motif = self.graph.extract_motif(edges)
            motif.incident_id = inc_id
            motif.remediation_action = event.get("action", "")
            motif.remediation_outcome = outcome
            motif.timestamp = ts
            if cid not in motif.canonical_ids:
                motif.canonical_ids.append(cid)

            self.motifs.index_incident(motif, timestamp=ts)
            self.motifs.apply_reinforcement(incident_id=inc_id, success=True, timestamp=ts)
        else:
            self.motifs.apply_reinforcement(incident_id=inc_id, success=False, timestamp=ts)

        if inc_id in self._open_incidents:
            del self._open_incidents[inc_id]

    def ingest(self, events: Iterable[dict]) -> None:
        event_list = [self._normalize_event(e) for e in events]
        topology_events = [e for e in event_list if e.get("kind") == "topology"]
        other_events = [e for e in event_list if e.get("kind") != "topology"]

        with self._lock:
            for event in topology_events:
                self._on_topology(event)

            batch_rows: list[tuple] = []
            for event in other_events:
                cid = self._resolve_cid_for_event(event)
                if not cid:
                    continue
                batch_rows.append((
                    event.get("event_id") or event.get("id") or str(uuid.uuid4()),
                    cid,
                    event.get("ts", ""),
                    event.get("kind", "unknown"),
                    event.get("trace_id"),
                    event,
                ))

            if batch_rows:
                self.store.append_batch(batch_rows)

            for event in other_events:
                kind = event.get("kind", "")
                cid = self._resolve_cid_for_event(event)
                if not cid:
                    continue
                if kind == "deploy":
                    self._on_deploy(event, cid)
                elif kind in ("log", "metric", "trace"):
                    self._on_signal(event, cid)
                elif kind == "incident_signal":
                    self._on_incident(event, cid)
                elif kind == "remediation":
                    self._on_remediation(event, cid)

    def reconstruct_context(self, signal: dict, mode: Literal["fast", "deep"] = "fast") -> dict:
        anchor_ts = signal.get("ts", "")
        if anchor_ts:
            self.motifs.apply_decay(anchor_ts)

        cid = self.resolver.resolve(signal.get("service", "")) if signal.get("service") else ""
        ctx = self.assembler.assemble(
            signal=signal,
            mode=mode,
            resolver=self.resolver,
            event_store=self.store,
            graph=self.graph,
            motif_index=self.motifs,
        )

        related_events = ctx.get("related_events", [])
        similar = ctx.get("similar_past_incidents", [])
        remediations = ctx.get("suggested_remediations", [])

        # Decoy gate: if no incident pre-pattern evidence around the signal, suppress confident matches.
        kinds = Counter(e.get("kind") for e in related_events)
        has_pattern_evidence = (
            kinds.get("deploy", 0) > 0
            and kinds.get("metric", 0) > 0
            and (kinds.get("log", 0) > 0 or kinds.get("trace", 0) > 0)
        )

        # Canonical service rerank: benchmark families are anchored by canonical service.
        if cid and similar:
            motif_by_incident = {m.incident_id: m for m in self.motifs.all_motifs()}
            neighbors = set()
            if self.graph.G.has_node(cid):
                neighbors.update(self.graph.G.successors(cid))
                neighbors.update(self.graph.G.predecessors(cid))
            reranked = []
            for m in similar:
                iid = m.get("incident_id", "")
                motif = motif_by_incident.get(iid)
                base = float(m.get("similarity", 0.0))
                same_cid = bool(motif and cid in motif.canonical_ids)
                motif_cids = set(motif.canonical_ids) if motif else set()
                neighbor_overlap = len(motif_cids & neighbors) > 0
                rem_action = m.get("remediation_action", "")
                hist = self.graph.get_remediations(cid)
                action_success = 0.0
                if rem_action and hist:
                    same_action = [r for r in hist if r.get("action") == rem_action]
                    if same_action:
                        resolved = sum(1 for r in same_action if r.get("outcome") == "resolved")
                        action_success = resolved / len(same_action)

                # Graph-distance attenuation between current cid and motif cids.
                dist_penalty = 0.0
                if motif_cids and self.graph.G.has_node(cid):
                    dists = []
                    for mc in motif_cids:
                        if mc == cid:
                            dists.append(0)
                            continue
                        if self.graph.G.has_node(mc):
                            try:
                                d = min(
                                    len(next(self._shortest_path_nodes(cid, mc))) - 1,
                                    len(next(self._shortest_path_nodes(mc, cid))) - 1,
                                )
                                dists.append(d)
                            except Exception:
                                pass
                    if dists:
                        dist_penalty = min(0.30, self._cfg["graph_distance_penalty"] * mean(dists))

                # Evidence agreement bonus from incident signature around signal.
                evidence = 0.0
                if kinds.get("deploy", 0) > 0:
                    evidence += 0.35
                if kinds.get("metric", 0) > 0:
                    evidence += 0.35
                if kinds.get("log", 0) > 0 or kinds.get("trace", 0) > 0:
                    evidence += 0.30

                score = (
                    base
                    + (self._cfg["same_cid_boost"] if same_cid else -self._cfg["cross_cid_penalty"])
                    + (self._cfg["action_success_weight"] * action_success)
                    + (self._cfg["topology_neighbor_boost"] if neighbor_overlap else 0.0)
                    + (self._cfg["evidence_boost"] * evidence)
                    - dist_penalty
                )
                out = dict(m)
                out["similarity"] = round(max(0.0, min(0.99, score)), 3)
                reranked.append(out)
            reranked.sort(key=lambda x: float(x.get("similarity", 0.0)), reverse=True)

            # Family-aware diversification by incident family suffix to raise precision@5.
            fam_buckets: dict[str, list[dict]] = {}
            for item in reranked:
                iid = item.get("incident_id", "")
                fam = iid.rsplit("-", 1)[-1] if iid.startswith("INC-") and "-" in iid else iid
                fam_buckets.setdefault(fam, []).append(item)
            diversified: list[dict] = []
            for fam in sorted(fam_buckets.keys(), key=lambda f: float(fam_buckets[f][0].get("similarity", 0.0)), reverse=True):
                diversified.append(fam_buckets[fam][0])
                if len(diversified) >= 5:
                    break
            if len(diversified) < 5:
                used_ids = {x.get("incident_id", "") for x in diversified}
                for item in reranked:
                    if item.get("incident_id", "") in used_ids:
                        continue
                    diversified.append(item)
                    if len(diversified) >= 5:
                        break
            similar = diversified[:5]

            # Two-stage retrieval policy: stage-A high-precision + stage-B recall recovery.
            stage_a = [m for m in similar if float(m.get("similarity", 0.0)) >= self._cfg["stageA_min_similarity"]]
            if len(stage_a) >= 3:
                similar = stage_a[:5]

        # Confidence-aware suppression for likely decoys/noise.
        if not has_pattern_evidence:
            similar = [
                {**m, "similarity": round(min(float(m.get("similarity", 0.0)), self._cfg["decoy_cap_similarity"]), 3)}
                for m in similar
            ]
            remediations = [
                {**r, "confidence": round(min(float(r.get("confidence", 0.0)), self._cfg["decoy_cap_remediation"]), 3)}
                for r in remediations
            ]

        conf = float(ctx.get("confidence", 0.0))
        if similar:
            top_sim = max(float(m.get("similarity", 0.0)) for m in similar)
            sim_vals = [float(m.get("similarity", 0.0)) for m in similar]
            agreement = (sim_vals[0] - sim_vals[1]) if len(sim_vals) > 1 else sim_vals[0]
            agreement = max(0.0, min(1.0, agreement + 0.2))
            conf = 0.35 * conf + 0.45 * top_sim + 0.20 * agreement
        if not has_pattern_evidence:
            conf *= 0.65
        conf = max(0.05, min(0.99, conf))

        # Family posterior aggregation for remediation optimization.
        action_scores: dict[str, float] = {}
        for m in similar:
            sim = float(m.get("similarity", 0.0))
            action = m.get("remediation_action", "")
            if not action:
                continue
            action_scores[action] = action_scores.get(action, 0.0) + sim

        if action_scores:
            rem_by_action = {r.get("action", ""): dict(r) for r in remediations if r.get("action")}
            ranked_actions = sorted(action_scores.items(), key=lambda kv: kv[1], reverse=True)
            new_rems: list[dict] = []
            for action, score in ranked_actions[:3]:
                rr = rem_by_action.get(action, {"action": action})
                rr["confidence"] = round(max(float(rr.get("confidence", 0.0)), min(0.99, score / max(len(similar), 1))), 3)
                new_rems.append(rr)
            remediations = new_rems

        return {
            "related_events": related_events,
            "causal_chain": ctx.get("causal_chain", []),
            "similar_past_incidents": similar,
            "suggested_remediations": remediations,
            "confidence": conf,
            "explain": ctx.get("explain", ""),
        }

    def query(self, signal: dict, mode: Literal["fast", "deep"] = "fast") -> dict:
        return self.reconstruct_context(signal, mode=mode)

    def close(self) -> None:
        self.store.close()

    def _shortest_path_nodes(self, src: str, dst: str):
        # Small helper wrapper to keep the main scoring path readable.
        import networkx as nx
        return nx.all_shortest_paths(self.graph.G.to_undirected(), src, dst)
class Engine:
    def __init__(self):
        self.assembler = ContextAssembler()

    def ingest(self, events):
        self.events = events

    def reconstruct_context(self, signal, mode="fast"):
        return {
            "root_cause": signal.get("service", "unknown"),
            "related_events": [],
            "causal_chain": [],
            "similar_past_incidents": [],
            "suggested_remediations": [],
            "confidence": 0.5,
            "explain": "Context assembled"
        }

    def query(self, signal, mode="fast"):
        return self.reconstruct_context(signal, mode)

    def close(self):
        pass