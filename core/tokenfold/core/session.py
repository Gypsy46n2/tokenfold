"""Session state: entity table, seen-content hashes, folded-history store.

A session is keyed by the adapter (conversation id, or a hash of the first
user message). Everything here is local and persisted under sessions_dir()
so a restarted proxy keeps its folds and entity tables.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from typing import Optional

from ..paths import sessions_dir

# Proper-noun-ish phrases: 2+ capitalized words, or CamelCase products
_ENTITY = re.compile(r"\b([A-Z][a-z0-9]+(?:\s+[A-Z][a-z0-9]+){1,3})\b")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


@dataclass
class Entity:
    code: str          # "E1"
    name: str          # "Agent Manager"
    count: int = 0


@dataclass
class Fold:
    """A folded span of history: turns [start, end] replaced by summary."""
    start: int
    end: int
    summary: str
    kind: str = "extractive"      # or "abstractive"
    original_hashes: list[str] = field(default_factory=list)


class Session:
    def __init__(self, session_id: str):
        self.id = session_id
        self.path = sessions_dir() / f"{session_id}.json"
        self.entities: dict[str, Entity] = {}
        self.entity_counts: Counter = Counter()
        self.seen_blobs: dict[str, dict] = {}    # hash -> {turn, kind, len}
        self.folds: list[Fold] = []
        self.originals: dict[str, str] = {}      # hash -> original text (fold store)
        self.established_dict_version: int = 0   # highest DICT version sent
        self.used_codes: list[str] = []          # grow-only, first-use order
        self.digested: list[str] = []            # hashes of absorbed turns
        self.digest_entries: list[dict] = []     # [{"h": turn_hash, "line": text}]
        self.last_inject_hash: str = ""          # cache-hit tracking
        self.pending_expansions: list[str] = []  # refs the model asked to expand
        self.turn: int = 0
        self.updated: float = time.time()
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.entities = {c: Entity(**e) for c, e in raw.get("entities", {}).items()}
                self.entity_counts = Counter(raw.get("entity_counts", {}))
                self.seen_blobs = raw.get("seen_blobs", {})
                self.folds = [Fold(**f) for f in raw.get("folds", [])]
                self.originals = raw.get("originals", {})
                self.established_dict_version = raw.get("established_dict_version", 0)
                self.used_codes = raw.get("used_codes", [])
                self.digested = raw.get("digested", [])
                self.digest_entries = raw.get("digest_entries", [])
                self.last_inject_hash = raw.get("last_inject_hash", "")
                self.pending_expansions = raw.get("pending_expansions", [])
                self.turn = raw.get("turn", 0)
            except Exception:
                pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "entities": {c: asdict(e) for c, e in self.entities.items()},
            "entity_counts": dict(self.entity_counts),
            "seen_blobs": self.seen_blobs,
            "folds": [asdict(f) for f in self.folds],
            "originals": self.originals,
            "established_dict_version": self.established_dict_version,
            "used_codes": self.used_codes,
            "digested": self.digested,
            "digest_entries": self.digest_entries,
            "last_inject_hash": self.last_inject_hash,
            "pending_expansions": self.pending_expansions,
            "turn": self.turn,
            "updated": time.time(),
        }, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # -- entity learning -------------------------------------------------
    _STOP = {"The", "A", "An", "This", "That", "Our", "Your", "My", "Its",
             "In", "On", "At", "For", "With", "And", "Or", "But", "If"}

    def observe_entities(self, text: str) -> None:
        for m in _ENTITY.finditer(text):
            name = m.group(1)
            # strip leading capitalized stopwords ("The Agent Manager")
            parts = name.split()
            while parts and parts[0] in self._STOP:
                parts = parts[1:]
            if len(parts) < 2:
                continue
            name = " ".join(parts)
            if len(name) < 8:               # too short to alias profitably
                continue
            self.entity_counts[name] += 1
            if self.entity_counts[name] >= 2 and not any(
                    e.name == name for e in self.entities.values()):
                code = f"E{len(self.entities) + 1}"
                self.entities[code] = Entity(code=code, name=name)

    def entity_pairs(self) -> list[tuple[str, str]]:
        """(name, code), longest names first (avoid partial matches)."""
        pairs = [(e.name, e.code) for e in self.entities.values()]
        pairs.sort(key=lambda p: -len(p[0]))
        return pairs

    def entity_block(self) -> str:
        """Byte-stable entity definitions (all of them, code order): the
        upstream is stateless, so definitions ride on every request that
        uses E-codes. Append-only ordering keeps the block prefix-stable."""
        if not self.entities:
            return ""
        ents = sorted(self.entities.values(), key=lambda e: int(e.code[1:]))
        return "ENT: " + "; ".join(f"{e.code}={e.name}" for e in ents)

    # -- repeated-content dedup -----------------------------------------
    def note_blob(self, text: str, kind: str) -> tuple[str, bool]:
        """Register a large content blob; returns (hash, seen_before)."""
        h = content_hash(text)
        seen = h in self.seen_blobs
        if not seen:
            self.seen_blobs[h] = {"turn": self.turn, "kind": kind, "len": len(text)}
            self.originals[h] = text
        return h, seen


def session_id_for(messages: list[dict]) -> str:
    """Stable id: hash of the first user message + system prompt head."""
    first_user = next((m for m in messages if m.get("role") == "user"), None)
    sys_msg = next((m for m in messages if m.get("role") == "system"), None)
    basis = ((sys_msg or {}).get("content") or "")[:400] + \
            ((first_user or {}).get("content") or "")[:400]
    return content_hash(basis or str(time.time()))
