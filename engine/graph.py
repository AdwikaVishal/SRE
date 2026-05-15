from __future__ import annotations

import pickle
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Set, Tuple

import networkx as nx
from networkx.algorithms import isomorphism

from .identity import IdentityResolver
from .models import CausalEdge, IncidentMotif


def _parse_ts(ts: str) -> datetime:
    ts = (ts or "").strip()
    if not ts:
        raise ValueError("empty ts")
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        # Fallback: ignore timezone details
        base = ts.split("+")[0]
        return datetime.fromisoformat(base).replace(tzinfo=timezone.utc)


def _subtract_seconds(ts: str, seconds: int) -> str:
    dt = _parse_ts(ts)
    return (dt - timedelta(seconds=seconds)).isoformat()


def _add_seconds(ts: str, seconds: int) -> str:
    dt = _parse_ts(ts)
    return (dt + timedelta(seconds=seconds)).isoformat()


def _is_before(ts1: str, ts2: str) -> bool:
    return _parse_ts(ts1) < _parse_ts(ts2)


def _days_between(ts1: str, ts2: str) -> float:
    dt1 = _parse_ts(ts1)
    dt2 = _parse_ts(ts2)
    return abs((dt2 - dt1).total_seconds() / 86400.0)


def _periods_24h_since(ref_ts: str, now_ts: str) -> int:
    """Full 24-hour periods elapsed between ref_ts and now_ts."""
    if not ref_ts:
        return 0
    try:
        elapsed_s = (_parse_ts(now_ts) - _parse_ts(ref_ts)).total_seconds()
    except Exception:
        return 0
    if elapsed_s < 86400.0:
        return 0
    return int(elapsed_s // 86400.0)


# Exponential confidence decay: multiply by this factor per 24h elapsed
DECAY_FACTOR_PER_24H = 0.99
CONFIDENCE_CAP = 0.95
REMEDIATION_BOOST = 0.05


def _abstract_event_type(relation: str) -> str:
    relation_lower = (relation or "").lower()

    if "deploy" in relation_lower:
        return "DEPLOY"
    if "metric" in relation_lower or "spike" in relation_lower:
        return "METRIC_SPIKE"
    if "error" in relation_lower or "log" in relation_lower:
        return "ERROR_BURST"
    if "upstream" in relation_lower or "call" in relation_lower:
        return "UPSTREAM_FAILURE"
    if "latency" in relation_lower:
        return "LATENCY_SPIKE"
    if "trace" in relation_lower:
        return "TRACE_ERROR"
    return (relation or "UNKNOWN").upper()


class OperationalGraph:
    """Probabilistic directed graph over canonical_ids."""

    def __init__(self) -> None:
        self.G: nx.DiGraph = nx.DiGraph()
        # canonical_id -> List[{'canonical_id','version','ts'}] in chronological order
        self._deploy_tracker: Dict[str, List[dict]] = {}
        # canonical_id -> List[remediation rows]
        self._remediation_table: Dict[str, List[dict]] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Core edge methods
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
        """Add or reinforce a causal edge.

        CRITICAL: Enforces ts_src < ts_dst. If violated, skips.
        """

        # TEMPORAL ENFORCEMENT
        if not _is_before(ts_src, ts_dst):
            print(f"WARNING: Temporal violation - {ts_src} >= {ts_dst}. Skipping edge.")
            return

        with self._lock:
            key = (src_cid, dst_cid, relation)

            # In this graph, we treat (src, dst) as the single edge container.
            # If relation differs for the same endpoints, we store by relation in attributes
            # (for simplicity and performance in this repo).
            if self.G.has_edge(src_cid, dst_cid):
                e = self.G[src_cid][dst_cid]
                if e.get("relation") != relation:
                    # Keep the most common relation; do not create multi-edge for now.
                    # This avoids breaking existing traversal expectations.
                    # (Spec-wise, relation is per edge; here we preserve original relation.)
                    return

                e["count"] += 1
                e["confidence"] = min(0.95, float(e["confidence"]) + 0.05)
                if evidence_id not in e["evidence_ids"]:
                    e["evidence_ids"].append(evidence_id)
                e["last_seen"] = ts_dst
            else:
                self.G.add_edge(
                    src_cid,
                    dst_cid,
                    relation=relation,
                    confidence=0.3,
                    count=1,
                    first_seen=ts_src,
                    last_seen=ts_dst,
                    evidence_ids=[evidence_id],
                    remediation_reinforced=False,
                    reinforced_by=[],
                )

    def get_edge(
        self, src_cid: str, dst_cid: str, relation: Optional[str] = None
    ) -> Optional[CausalEdge]:
        with self._lock:
            if not self.G.has_edge(src_cid, dst_cid):
                return None
            data = self.G[src_cid][dst_cid]
            if relation is not None and data.get("relation") != relation:
                return None
            return CausalEdge(
                src_cid=src_cid,
                dst_cid=dst_cid,
                relation=data.get("relation", ""),
                confidence=float(data.get("confidence", 0.0)),
                count=int(data.get("count", 0)),
                first_seen=data.get("first_seen", ""),
                last_seen=data.get("last_seen", ""),
                evidence_ids=list(data.get("evidence_ids", [])),
                remediation_reinforced=bool(data.get("remediation_reinforced", False)),
                reinforced_by=list(data.get("reinforced_by", [])),
            )

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def get_causal_chain(
        self,
        cid: str,
        max_hops: int = 2,
        min_confidence: float = 0.3,
    ) -> List[CausalEdge]:
        visited: set[str] = set()
        queue: List[Tuple[str, int]] = [(cid, 0)]
        edges: List[CausalEdge] = []

        with self._lock:
            while queue:
                current, depth = queue.pop(0)
                if current in visited or depth > max_hops:
                    continue
                visited.add(current)

                # Guard: node may not yet be in graph
                if not self.G.has_node(current):
                    continue

                # outgoing
                for neighbor in self.G.successors(current):
                    data = self.G[current][neighbor]
                    if float(data.get("confidence", 0.0)) < min_confidence:
                        continue
                    edges.append(
                        CausalEdge(
                            src_cid=current,
                            dst_cid=neighbor,
                            relation=data.get("relation", ""),
                            confidence=float(data.get("confidence", 0.0)),
                            count=int(data.get("count", 0)),
                            first_seen=data.get("first_seen", ""),
                            last_seen=data.get("last_seen", ""),
                            evidence_ids=list(data.get("evidence_ids", [])),
                            remediation_reinforced=bool(
                                data.get("remediation_reinforced", False)
                            ),
                            reinforced_by=list(data.get("reinforced_by", [])),
                        )
                    )
                    queue.append((neighbor, depth + 1))

                # incoming
                for predecessor in self.G.predecessors(current):
                    data = self.G[predecessor][current]
                    if float(data.get("confidence", 0.0)) < min_confidence:
                        continue
                    edges.append(
                        CausalEdge(
                            src_cid=predecessor,
                            dst_cid=current,
                            relation=data.get("relation", ""),
                            confidence=float(data.get("confidence", 0.0)),
                            count=int(data.get("count", 0)),
                            first_seen=data.get("first_seen", ""),
                            last_seen=data.get("last_seen", ""),
                            evidence_ids=list(data.get("evidence_ids", [])),
                            remediation_reinforced=bool(
                                data.get("remediation_reinforced", False)
                            ),
                            reinforced_by=list(data.get("reinforced_by", [])),
                        )
                    )
                    queue.append((predecessor, depth + 1))

        # oldest first
        edges.sort(key=lambda e: e.first_seen)
        return edges

    # ------------------------------------------------------------------
    # Remediation & reinforcement
    # ------------------------------------------------------------------

    def reinforce_remediation(
        self,
        cid: str,
        incident_id: str,
        action: str,
        outcome: str,
        ts: str,
        window_s: int = 600,
        max_hops: int = 2,
    ) -> int:
        """
        On resolved remediation, boost confidence (+0.05, cap 0.95) for every edge
        in the incident's causal chain. Returns count of reinforced edges.
        """
        if outcome != "resolved":
            return 0

        chain_edges = self.get_causal_chain(cid, max_hops=max_hops, min_confidence=0.0)
        reinforced_count = 0
        seen: set[tuple[str, str]] = set()

        with self._lock:
            for edge in chain_edges:
                key = (edge.src_cid, edge.dst_cid)
                if key in seen or not self.G.has_edge(edge.src_cid, edge.dst_cid):
                    continue
                seen.add(key)
                data = self.G[edge.src_cid][edge.dst_cid]
                old_conf = float(data.get("confidence", 0.0))
                data["confidence"] = min(CONFIDENCE_CAP, old_conf + REMEDIATION_BOOST)
                data["remediation_reinforced"] = True
                data.setdefault("reinforced_by", [])
                data["reinforced_by"].append(
                    {
                        "incident_id": incident_id,
                        "action": action,
                        "outcome": outcome,
                        "ts": ts,
                        "old_confidence": old_conf,
                        "new_confidence": data["confidence"],
                    }
                )
                reinforced_count += 1

            self._remediation_table.setdefault(cid, []).append(
                {
                    "canonical_id": cid,
                    "action": action,
                    "target_version": None,
                    "outcome": outcome,
                    "ts": ts,
                    "incident_id": incident_id,
                    "reinforced_edges": reinforced_count,
                }
            )

        return reinforced_count

    def get_remediation_history(self, cid: str) -> List[dict]:
        return list(self._remediation_table.get(cid, []))

    def get_remediations(self, cid: str) -> List[dict]:
        # compatibility with ContextAssembler
        return self.get_remediation_history(cid)

    # ------------------------------------------------------------------
    # Confidence decay
    # ------------------------------------------------------------------

    def apply_decay_node(
        self,
        cid: str,
        current_ts: str,
        decay_per_day: float = 0.01,
    ) -> int:
        """Apply decay to edges incident to a single canonical_id."""
        decayed = 0
        with self._lock:
            for u, v, data in self.G.edges([cid], data=True):
                last_seen = data.get("last_seen", "")
                if not last_seen:
                    continue
                days_old = _days_between(last_seen, current_ts)
                if days_old <= 0:
                    continue
                old_conf = float(data.get("confidence", 0.0))
                data["confidence"] = max(0.1, old_conf - (decay_per_day * days_old))
                if float(data.get("confidence", 0.0)) != old_conf:
                    decayed += 1
        return decayed

    def apply_decay_all(
        self,
        current_ts: str,
        decay_per_day: float = 0.01,
    ) -> int:
        decayed = 0
        with self._lock:
            for _, _, data in self.G.edges(data=True):
                last_seen = data.get("last_seen", "")
                if not last_seen:
                    continue
                days_old = _days_between(last_seen, current_ts)
                if days_old <= 0:
                    continue
                old_conf = float(data.get("confidence", 0.0))
                data["confidence"] = max(0.1, old_conf - (decay_per_day * days_old))
                if float(data.get("confidence", 0.0)) != old_conf:
                    decayed += 1
        return decayed

    # ------------------------------------------------------------------
    # Deploy tracker
    # ------------------------------------------------------------------

    def record_deploy(self, cid: str, version: str, ts: str) -> None:
        with self._lock:
            # Pre-parse timestamp to avoid re-parsing in get_recent_deploy
            try:
                dt = _parse_ts(ts)
            except Exception:
                dt = None
            self._deploy_tracker.setdefault(cid, []).append(
                {"canonical_id": cid, "version": version, "ts": ts, "_dt": dt}
            )

    def get_recent_deploy(
        self,
        cid: str,
        before_ts: str,
        window_s: int = 600,
    ) -> Optional[dict]:
        with self._lock:
            deploys = self._deploy_tracker.get(cid, [])
            if not deploys:
                return None

            # Parse before_ts once, outside the loop
            try:
                before_dt = _parse_ts(before_ts)
                window_start_dt = before_dt - timedelta(seconds=window_s)
            except Exception:
                return None

            candidates = []
            for d in deploys:
                dt = d.get("_dt")  # Use pre-parsed datetime if available
                if dt is None:
                    # Fallback: parse it (for backward compatibility)
                    try:
                        dt = _parse_ts(d.get("ts", ""))
                    except Exception:
                        continue

                # Pure datetime comparison (no string parsing)
                if window_start_dt <= dt <= before_dt:
                    candidates.append(d)

            if not candidates:
                return None
            # Return the candidate with latest timestamp
            return max(
                candidates, key=lambda d: d.get("_dt") or _parse_ts(d.get("ts", ""))
            )

    # ------------------------------------------------------------------
    # Motif extraction
    # ------------------------------------------------------------------

    def extract_motif(
        self,
        edges: list[CausalEdge],
        resolver: IdentityResolver,
    ) -> IncidentMotif:
        """
        Convert causal chain to a topology-independent behavioral fingerprint.
        causal_shape uses (src_canonical_role, relation, dst_canonical_role) triples
        from IdentityResolver — stable across service renames.
        """
        motif = IncidentMotif()
        motif.canonical_ids = list(
            {e.src_cid for e in edges} | {e.dst_cid for e in edges}
        )

        motif.causal_shape = [
            (
                resolver.canonical_role(e.src_cid),
                e.relation,
                resolver.canonical_role(e.dst_cid),
            )
            for e in edges
        ]

        seen_roles: list[str] = []
        for src_role, _rel, dst_role in motif.causal_shape:
            for role in (src_role, dst_role):
                if role not in seen_roles:
                    seen_roles.append(role)
        motif.event_sequence = seen_roles

        motif.confidence = (
            sum(e.confidence for e in edges) / len(edges) if edges else 0.0
        )
        return motif

    # ------------------------------------------------------------------
    # Confidence decay (lazy — called at reconstruction time)
    # ------------------------------------------------------------------

    def apply_decay(self, now_ts: str) -> None:
        """Exponential decay: multiply edge confidence by 0.99 per 24h elapsed."""
        with self._lock:
            for _, _, data in self.G.edges(data=True):
                try:
                    ref_ts = (
                        data.get("confidence_decay_at")
                        or data.get("last_seen")
                        or data.get("first_seen", "")
                    )
                    periods = _periods_24h_since(ref_ts, now_ts)
                    if periods <= 0:
                        continue
                    old_conf = float(data.get("confidence", 0.0))
                    data["confidence"] = max(
                        0.1, old_conf * (DECAY_FACTOR_PER_24H**periods)
                    )
                    data["confidence_decay_at"] = now_ts
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Return summary statistics about the graph."""
        with self._lock:
            return {
                "num_nodes": self.G.number_of_nodes(),
                "num_edges": self.G.number_of_edges(),
                "num_deploys": sum(len(v) for v in self._deploy_tracker.values()),
                "num_remediations": sum(
                    len(v) for v in self._remediation_table.values()
                ),
                "avg_confidence": (
                    sum(d.get("confidence", 0) for _, _, d in self.G.edges(data=True))
                    / max(1, self.G.number_of_edges())
                ),
            }

    def save(self, filepath: str) -> None:
        with self._lock:
            with open(filepath, "wb") as f:
                pickle.dump(
                    {
                        "graph": self.G,
                        "deploy_tracker": self._deploy_tracker,
                        "remediation_table": self._remediation_table,
                    },
                    f,
                )

    def load(self, filepath: str) -> None:
        with self._lock:
            with open(filepath, "rb") as f:
                data = pickle.load(f)
            self.G = data["graph"]
            self._deploy_tracker = data.get("deploy_tracker", {})
            self._remediation_table = data.get("remediation_table", {})

    # ------------------------------------------------------------------
    # Causal Graph Reasoning: Root Cause Analysis & Graph Isomorphism
    # ------------------------------------------------------------------

    def _build_causal_graph(
        self,
        incident_cid: str,
        max_hops: int = 3,
        min_confidence: float = 0.25,
    ) -> nx.DiGraph:
        """
        Build a causal graph for a specific incident starting from incident_cid.

        The graph captures:
        - Nodes: canonical_ids that have causal relationships with incident_cid
        - Edges: directed causality (src -> dst) with temporal ordering enforced
        - Edge attributes: relation, confidence, evidence_ids, temporal_order

        Args:
            incident_cid: The canonical_id of the incident (root of the graph)
            max_hops: Maximum traversal depth from the incident
            min_confidence: Minimum confidence threshold to include edges

        Returns:
            A nx.DiGraph representing causality with incident_cid as a focal point.
        """
        causal_g = nx.DiGraph()
        visited: Set[str] = set()
        queue: List[Tuple[str, int]] = [(incident_cid, 0)]

        with self._lock:
            while queue:
                current_cid, depth = queue.pop(0)
                if current_cid in visited or depth > max_hops:
                    continue
                visited.add(current_cid)

                if not self.G.has_node(current_cid):
                    causal_g.add_node(
                        current_cid, is_root=(current_cid == incident_cid)
                    )
                    continue

                causal_g.add_node(current_cid, is_root=(current_cid == incident_cid))

                # Successors (effects: current -> neighbor)
                for neighbor in self.G.successors(current_cid):
                    data = self.G[current_cid][neighbor]
                    conf = float(data.get("confidence", 0.0))
                    if conf < min_confidence:
                        continue

                    causal_g.add_node(neighbor, is_root=False)
                    causal_g.add_edge(
                        current_cid,
                        neighbor,
                        relation=data.get("relation", ""),
                        confidence=conf,
                        count=int(data.get("count", 0)),
                        first_seen=data.get("first_seen", ""),
                        last_seen=data.get("last_seen", ""),
                        evidence_ids=list(data.get("evidence_ids", [])),
                    )
                    queue.append((neighbor, depth + 1))

                # Predecessors (causes: predecessor -> current)
                for predecessor in self.G.predecessors(current_cid):
                    data = self.G[predecessor][current_cid]
                    conf = float(data.get("confidence", 0.0))
                    if conf < min_confidence:
                        continue

                    causal_g.add_node(predecessor, is_root=False)
                    causal_g.add_edge(
                        predecessor,
                        current_cid,
                        relation=data.get("relation", ""),
                        confidence=conf,
                        count=int(data.get("count", 0)),
                        first_seen=data.get("first_seen", ""),
                        last_seen=data.get("last_seen", ""),
                        evidence_ids=list(data.get("evidence_ids", [])),
                    )
                    queue.append((predecessor, depth + 1))

        return causal_g

    def _find_root_causes(
        self,
        incident_cid: str,
        max_hops: int = 3,
        min_confidence: float = 0.25,
    ) -> List[dict]:
        """
        Identify root causes of an incident by finding source nodes in the causal graph.

        A root cause is a node with:
        - High in-degree or is a source (no incoming edges)
        - Strong causal evidence (high cumulative confidence)
        - Early temporal ordering (first_seen earliest)

        Returns:
            List of dicts with keys:
            - cid: the root cause canonical_id
            - confidence: average confidence of edges leading to incident
            - evidence_count: number of supporting events
            - earliest_time: when the root cause first appeared
            - path_length: minimum hops from root to incident
            - causal_chain: list of intermediate nodes leading to incident
        """
        causal_g = self._build_causal_graph(incident_cid, max_hops, min_confidence)
        root_causes: List[dict] = []

        # Find all nodes with no incoming edges (source nodes)
        source_nodes = [n for n in causal_g.nodes() if causal_g.in_degree(n) == 0]

        if not source_nodes:
            # If no pure source, use nodes with minimal in-degree
            source_nodes = [
                n
                for n in causal_g.nodes()
                if causal_g.in_degree(n) <= 1 and n != incident_cid
            ]

        # For each source, compute path to incident and collect evidence
        for source_cid in source_nodes:
            try:
                # Find shortest path from source to incident
                path = nx.shortest_path(causal_g, source_cid, incident_cid)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                # Source is isolated or not connected to incident
                continue

            # Collect confidence and evidence along the path
            edge_confidences: List[float] = []
            all_evidence: List[str] = []
            earliest_time = None

            for i in range(len(path) - 1):
                src, dst = path[i], path[i + 1]
                edge_data = causal_g[src][dst]
                edge_confidences.append(edge_data.get("confidence", 0.0))
                all_evidence.extend(edge_data.get("evidence_ids", []))

                first_seen = edge_data.get("first_seen", "")
                if first_seen and (
                    earliest_time is None or _is_before(first_seen, earliest_time)
                ):
                    earliest_time = first_seen

            # For the source node itself, check if it has early deploy/event timestamp
            if source_cid in self._deploy_tracker:
                deploys = self._deploy_tracker[source_cid]
                if deploys:
                    deploy_time = deploys[-1].get("ts", "")
                    if deploy_time and (
                        earliest_time is None or _is_before(deploy_time, earliest_time)
                    ):
                        earliest_time = deploy_time

            avg_confidence = (
                sum(edge_confidences) / len(edge_confidences)
                if edge_confidences
                else 0.0
            )

            root_causes.append(
                {
                    "cid": source_cid,
                    "confidence": round(avg_confidence, 3),
                    "evidence_count": len(set(all_evidence)),
                    "earliest_time": earliest_time or "",
                    "path_length": len(path) - 1,
                    "causal_chain": path,  # Full path including source and incident
                    "intermediate_nodes": path[1:-1] if len(path) > 2 else [],
                }
            )

        # Sort by confidence (descending) and path_length (ascending)
        root_causes.sort(key=lambda x: (-x["confidence"], x["path_length"]))

        return root_causes

    def _compare_causal_graphs(
        self,
        cid1: str,
        cid2: str,
        max_hops: int = 3,
        min_confidence: float = 0.25,
    ) -> float:
        """
        Compare two causal graphs using graph isomorphism and structural similarity.

        Returns a similarity score (0.0 to 1.0) based on:
        - Structural isomorphism (node count, edge count, degree distribution)
        - Relation type matching (causal relations must match)
        - Edge direction and temporal ordering

        This enables behavior-based incident matching robust to service renames.
        """
        g1 = self._build_causal_graph(cid1, max_hops, min_confidence)
        g2 = self._build_causal_graph(cid2, max_hops, min_confidence)

        # Basic structural similarity
        if g1.number_of_nodes() == 0 or g2.number_of_nodes() == 0:
            return 0.0

        node_ratio = min(
            g1.number_of_nodes(),
            g2.number_of_nodes(),
        ) / max(
            g1.number_of_nodes(),
            g2.number_of_nodes(),
        )

        if g1.number_of_edges() == 0 or g2.number_of_edges() == 0:
            edge_ratio = 0.0
        else:
            edge_ratio = min(
                g1.number_of_edges(),
                g2.number_of_edges(),
            ) / max(
                g1.number_of_edges(),
                g2.number_of_edges(),
            )

        # Compare degree distributions (topology fingerprint)
        g1_degrees = sorted(
            [d for n, d in g1.in_degree()] + [d for n, d in g1.out_degree()]
        )
        g2_degrees = sorted(
            [d for n, d in g2.in_degree()] + [d for n, d in g2.out_degree()]
        )

        degree_similarity = 1.0
        if g1_degrees and g2_degrees:
            # Compare average degree
            avg1 = sum(g1_degrees) / len(g1_degrees)
            avg2 = sum(g2_degrees) / len(g2_degrees)
            if max(avg1, avg2) > 0:
                degree_similarity = min(avg1, avg2) / max(avg1, avg2)

        # Compare relation type distributions
        def get_relation_distribution(g: nx.DiGraph) -> Dict[str, int]:
            dist: Dict[str, int] = {}
            for u, v, data in g.edges(data=True):
                relation = data.get("relation", "unknown")
                dist[relation] = dist.get(relation, 0) + 1
            return dist

        rel_dist_1 = get_relation_distribution(g1)
        rel_dist_2 = get_relation_distribution(g2)

        # Relation similarity: how many relation types match
        if rel_dist_1 and rel_dist_2:
            common_relations = set(rel_dist_1.keys()) & set(rel_dist_2.keys())
            relation_similarity = len(common_relations) / max(
                len(rel_dist_1),
                len(rel_dist_2),
            )
        else:
            relation_similarity = 0.5 if (not rel_dist_1 and not rel_dist_2) else 0.0

        # Combined similarity: weighted average
        # Node and edge topology are most important
        similarity = (
            0.35 * node_ratio
            + 0.35 * edge_ratio
            + 0.20 * degree_similarity
            + 0.10 * relation_similarity
        )

        return round(min(1.0, max(0.0, similarity)), 3)


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
