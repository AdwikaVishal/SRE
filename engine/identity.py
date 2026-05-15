"""
Layer 1 — Identity Resolution

Maintains canonical entity IDs across all infrastructure mutations.
Every rename, alias, and dependency shift is recorded here.
This is the first thing built and the last thing touched.

RULE: Never store a raw service name in any downstream layer.
      Always resolve to canonical_id first.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class RenameEvent:
    old_name: str
    new_name: str
    ts: str
    canonical_id: str


class IdentityResolver:
    """
    Thread-safe canonical ID resolver.

    Maintains a bidirectional mapping between service names and stable
    canonical IDs. A service renamed N times always resolves to the same
    canonical_id.
    """

    def __init__(self) -> None:
        self._name_to_id: dict[str, str] = {}       # current_name → canonical_id
        self._id_to_names: dict[str, list[str]] = {} # canonical_id → [all names ever]
        self._rename_log: list[RenameEvent] = []
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, name: str) -> str:
        """
        First time we see a service. Returns canonical_id.
        Idempotent — safe to call multiple times for the same name.
        """
        with self._lock:
            return self._register_unsafe(name)

    def rename(self, old_name: str, new_name: str, ts: str) -> str:
        """
        Process a topology rename event. Returns canonical_id.
        Maps new_name to the same canonical_id as old_name.
        """
        with self._lock:
            cid = self._name_to_id.get(old_name) or self._register_unsafe(old_name)
            self._name_to_id[new_name] = cid
            if new_name not in self._id_to_names[cid]:
                self._id_to_names[cid].append(new_name)
            self._rename_log.append(RenameEvent(old_name, new_name, ts, cid))
            return cid

    def resolve(self, name: str) -> str:
        """
        Always returns canonical_id. Creates one if the service is unknown.
        NEVER returns None.
        """
        with self._lock:
            if name in self._name_to_id:
                return self._name_to_id[name]
            return self._register_unsafe(name)

    def current_name(self, canonical_id: str) -> str:
        """
        Returns the most recent name for a canonical_id (last in alias list).
        Used for display in Context output.
        """
        names = self._id_to_names.get(canonical_id, [])
        return names[-1] if names else canonical_id

    def all_names(self, canonical_id: str) -> list[str]:
        """Returns all historical names for a canonical_id."""
        return list(self._id_to_names.get(canonical_id, []))

    def rename_history(self, canonical_id: str) -> list[RenameEvent]:
        """Returns rename events for a specific canonical_id."""
        return [r for r in self._rename_log if r.canonical_id == canonical_id]

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name_to_id": self._name_to_id,
            "id_to_names": self._id_to_names,
            "rename_log": [
                {"old": r.old_name, "new": r.new_name, "ts": r.ts, "cid": r.canonical_id}
                for r in self._rename_log
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IdentityResolver":
        resolver = cls()
        resolver._name_to_id = data.get("name_to_id", {})
        resolver._id_to_names = data.get("id_to_names", {})
        resolver._rename_log = [
            RenameEvent(r["old"], r["new"], r["ts"], r["cid"])
            for r in data.get("rename_log", [])
        ]
        return resolver

    def save(self, path: str) -> None:
        with open(path, "w") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path: str) -> "IdentityResolver":
        with open(path) as f:
            return cls.from_dict(json.load(f))

    # ------------------------------------------------------------------
    # Internal helpers (must be called with lock held)
    # ------------------------------------------------------------------

    def _register_unsafe(self, name: str) -> str:
        if name in self._name_to_id:
            return self._name_to_id[name]
        cid = uuid4().hex[:8]
        self._name_to_id[name] = cid
        self._id_to_names[cid] = [name]
        return cid
