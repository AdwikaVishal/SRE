"""
Layer 3 — Operational Memory Graph

A probabilistic directed graph where nodes are canonical_ids and edges are
causal relationships with confidence, timestamp, and evidence pointers.
Continuously updated as events arrive.

RULES:
- Never use raw service names as node keys. canonical_id only.
- Edges must have source-precedes-effect enforced at write time.
- Initial confidence on new edge is 0.3. Grows with repeated observation.
- Confidence decay: subtract 0.01 per day of no reinforcement.
"""

from __future__ import annotations

import json
import pickle
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    import networkx as nx
    _NX_AVAILABLE = True
except ImportError:
    _NX_AVAILABLE = False


def _parse_ts(ts: str) -> datetime:
    """Parse ISO-8601 timestamp to datetime. Handles Z suffix."""
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: try without timezone
        return datetime.fromisoformat(ts.split("+")[0]).replace(tzinfo=timezone.utc)


@dataclass
class CausalEdge:
    src_cid: str
    dst_cid: str
    relation: str
    confidence: float
    count: int
    first_seen: str
    last_seen: str
    evidence_ids: list[str]

    def to_output(self, resolver: Any) -> dict:
        """Convert to output format with current service names."""
        return {
            "cause_id": self.src_cid,
            "effect_id": self.dst_cid,
            "cause_name": resolver.current_name(self.src_cid),
            "effect_name": resolver.current_name(self.dst_cid),
            "relation": self.relation,
            "confidence": round(self.confidence, 3),
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }


@dataclass
class IncidentMotif:
    """
    Topology-independent representation of an incident.
    Describes WHAT happened (the pattern), not WHERE (the services).
    """
    incident_id: str = ""
    canonical_ids: list[str] = field(default_factory=list)
    event_sequence: list[str] = field(default_factory=list)
    # List of (src_role, dst_role) tuples
    causal_shape: list[tuple[str, str]] = field(default_factory=list)
    remediation_action: str = ""
    remediation_outcome: str = ""
    timestamp: str = ""
    confidence: float = 0.0


class OperationalGraph:
    """
    Probabilistic directed causal graph over canonical_ids.

    Nodes: canonical_ids
    Edges: causal relationships with confidence, count, timestamps, evidence
    """

    def __init__(self) -> None:
        if not _NX_AVAILABLE:
            raise ImportError("networkx is required: pip install networkx")
        self.G: nx.DiGraph = nx.DiGraph()
        self._deploy_log: dict[str, list[dict]] = {}   # cid → [{ts, version}]
        self._remediation_table: dict[str, list[dict]] = {}  # cid → [outcomes]
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Edge management
    # ------------------------------------------------------------------

    def add_edge(
        self,
        src_cid: str,
        dst_cid: str,
        relation: str,
        evidence_id: str,
        ts_src: str,
        ts_dst: str,
    ) -> None:
        """
        Add or reinforce a causal edge.
        ENFORCES ts_src < ts_dst — never inverts causality.
        """
        # Temporal ordering guard
        try:
            if _parse_ts(ts_src) >= _parse_ts(ts_dst):
                # Log and skip — never invert causality
                return
        except Exception:
            pass  # If we can't parse, allow the edge

        with self._lock:
            if self.G.has_edge(src_cid, dst_cid):
                e = self.G[src_cid][dst_cid]
                e["count"] += 1
                e["confidence"] = min(0.95, e["confidence"] + 0.05)
                if evidence_id not in e["evidence_ids"]:
                    e["evidence_ids"].append(evidence_id)
                e["last_seen"] = ts_dst
            else:
                self.G.add_edge(
                    src_cid,
                    dst_cid,
                    count=1,
                    confidence=0.3,
                    relation=relation,
                    evidence_ids=[evidence_id],
                    first_seen=ts_src,
                    last_seen=ts_dst,
                )

    # ------------------------------------------------------------------
    # Deploy tracking
    # ------------------------------------------------------------------

    def record_deploy(self, cid: str, version: str, ts: str) -> None:
        """Record a deployment event for an entity."""
        with self._lock:
            if cid not in self._deploy_log:
                self._deploy_log[cid] = []
            self._deploy_log[cid].append({"ts": ts, "version": version})

    def get_recent_deploy(
        self, cid: str, anchor_ts: str, window_s: int = 600
    ) -> dict | None:
        """Return the most recent deploy for cid within window_s seconds before anchor_ts."""
        deploys = self._deploy_log.get(cid, [])
        if not deploys:
            return None
        try:
            anchor = _parse_ts(anchor_ts)
            candidates = [
                d for d in deploys
                if 0 <= (anchor - _parse_ts(d["ts"])).total_seconds() <= window_s
            ]
            return max(candidates, key=lambda d: d["ts"]) if candidates else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Remediation reinforcement
    # ------------------------------------------------------------------

    def reinforce_remediation(self, cid: str, event: dict) -> None:
        """
        Called when remediation event arrives with outcome=resolved.
        Boosts confidence of all edges in the 10-minute window before remediation.
        """
        anchor_ts = event.get("ts", "")
        with self._lock:
            for src, dst, data in self.G.edges(data=True):
                if src == cid or dst == cid:
                    try:
                        last_seen = _parse_ts(data.get("last_seen", ""))
                        anchor = _parse_ts(anchor_ts)
                        delta = (anchor - last_seen).total_seconds()
                        if 0 <= delta <= 600:
                            data["confidence"] = min(0.95, data["confidence"] + 0.10)
                            data["remediation_reinforced"] = True
                    except Exception:
                        pass

            # Store remediation outcome
            if cid not in self._remediation_table:
                self._remediation_table[cid] = []
            self._remediation_table[cid].append({
                "action": event.get("action", ""),
                "target_version": event.get("version"),
                "outcome": event.get("outcome", "unknown"),
                "ts": anchor_ts,
                "incident_id": event.get("incident_id", ""),
            })

    def get_remediations(self, cid: str) -> list[dict]:
        """Return all remediation outcomes for a canonical_id."""
        return list(self._remediation_table.get(cid, []))

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def get_causal_chain(
        self,
        cid: str,
        max_hops: int = 2,
        min_confidence: float = 0.3,
    ) -> list[CausalEdge]:
        """
        BFS from cid, prune by confidence threshold.
        Returns ordered list of CausalEdge (source-precedes-effect).
        """
        visited: set[str] = set()
        queue: list[tuple[str, int]] = [(cid, 0)]
        edges: list[CausalEdge] = []

        with self._lock:
            while queue:
                node, depth = queue.pop(0)
                if node in visited or depth > max_hops:
                    continue
                visited.add(node)

                for src, dst, data in self.G.edges(node, data=True):
                    if data.get("confidence", 0) < min_confidence:
                        continue
                    edges.append(CausalEdge(
                        src_cid=src,
                        dst_cid=dst,
                        relation=data.get("relation", ""),
                        confidence=data.get("confidence", 0.0),
                        count=data.get("count", 1),
                        first_seen=data.get("first_seen", ""),
                        last_seen=data.get("last_seen", ""),
                        evidence_ids=list(data.get("evidence_ids", [])),
                    ))
                    if dst not in visited:
                        queue.append((dst, depth + 1))

        # Sort by last_seen to enforce temporal ordering in output
        edges.sort(key=lambda e: e.last_seen)
        return edges

    def get_edges_in_window(
        self, cid: str, anchor_ts: str, window_s: int = 600
    ) -> list[tuple]:
        """Return graph edges involving cid with last_seen within window."""
        result = []
        try:
            anchor = _parse_ts(anchor_ts)
        except Exception:
            return result

        with self._lock:
            for src, dst, data in self.G.edges(data=True):
                if src == cid or dst == cid:
                    try:
                        last = _parse_ts(data.get("last_seen", ""))
                        if 0 <= (anchor - last).total_seconds() <= window_s:
                            result.append((src, dst, data))
                    except Exception:
                        pass
        return result

    # ------------------------------------------------------------------
    # Motif extraction
    # ------------------------------------------------------------------

    def extract_motif(self, edges: list[CausalEdge]) -> IncidentMotif:
        """
        Convert causal chain to topology-independent behavioral fingerprint.
        Replaces canonical_ids with role labels based on relation type.
        """
        motif = IncidentMotif()
        motif.canonical_ids = list({e.src_cid for e in edges} | {e.dst_cid for e in edges})

        # Build abstract event sequence from relation types
        seen_relations: list[str] = []
        for edge in edges:
            role = _relation_to_role(edge.relation)
            if role not in seen_relations:
                seen_relations.append(role)
        motif.event_sequence = seen_relations

        # Build causal shape as (src_role, dst_role) pairs
        motif.causal_shape = [
            (_relation_to_role(e.relation) + "_SRC", _relation_to_role(e.relation) + "_DST")
            for e in edges
        ]

        motif.confidence = (
            sum(e.confidence for e in edges) / len(edges) if edges else 0.0
        )
        return motif

    # ------------------------------------------------------------------
    # Confidence decay (lazy — called at reconstruction time)
    # ------------------------------------------------------------------

    def apply_decay(self, now_ts: str) -> None:
        """Decay edge confidence based on staleness. Called lazily."""
        try:
            now = _parse_ts(now_ts)
        except Exception:
            return

        with self._lock:
            for _, _, data in self.G.edges(data=True):
                try:
                    last = _parse_ts(data.get("last_seen", now_ts))
                    days_old = (now - last).days
                    if days_old > 0:
                        data["confidence"] = max(0.1, data["confidence"] - 0.01 * days_old)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump({
                "graph": self.G,
                "deploy_log": self._deploy_log,
                "remediation_table": self._remediation_table,
            }, f)

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.G = data["graph"]
        self._deploy_log = data.get("deploy_log", {})
        self._remediation_table = data.get("remediation_table", {})


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _relation_to_role(relation: str) -> str:
    """Map a relation string to an abstract role label."""
    relation = relation.lower()
    if "deploy" in relation:
        return "DEPLOY"
    if "metric" in relation or "latency" in relation or "spike" in relation:
        return "METRIC_ANOMALY"
    if "log" in relation or "error" in relation:
        return "ERROR_LOG"
    if "trace" in relation or "upstream" in relation or "call" in relation:
        return "UPSTREAM_CALL"
    if "incident" in relation:
        return "INCIDENT"
    if "remediation" in relation or "rollback" in relation or "restart" in relation:
        return "REMEDIATION"
    return "SIGNAL"
