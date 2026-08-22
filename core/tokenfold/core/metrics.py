"""Metrics store (SQLite) + encode cache.

Privacy: only counts, names of representations, latencies and hashes are
stored. Never message content, never protected-region contents.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict
from typing import Optional

from ..paths import metrics_db_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
  ts REAL, session TEXT, model TEXT, profile TEXT, exact INTEGER,
  original_tokens INTEGER, encoded_tokens INTEGER, dict_overhead INTEGER,
  saved INTEGER, pct REAL, latency_ms REAL, fallback INTEGER,
  representation TEXT, direction TEXT, provider TEXT, project TEXT
);
CREATE TABLE IF NOT EXISTS enc_cache(
  key TEXT PRIMARY KEY, encoded TEXT, representation TEXT, ts REAL
);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);
"""


class Metrics:
    def __init__(self, path: Optional[str] = None):
        self.path = path or str(metrics_db_path())
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)
            self._migrate(c)

    @staticmethod
    def _migrate(c: sqlite3.Connection) -> None:
        cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
        if "fallback_reason" not in cols:
            c.execute("ALTER TABLE events ADD COLUMN fallback_reason TEXT DEFAULT ''")
            # Old rows can't distinguish real errors from invariant reverts;
            # both were just fallback=1. Label them so the split stats don't
            # silently count them as clean.
            c.execute("UPDATE events SET fallback_reason='legacy' WHERE fallback=1")
        if "eff_overhead" not in cols:
            c.execute("ALTER TABLE events ADD COLUMN eff_overhead INTEGER DEFAULT 0")
            # Conservative backfill: judge old rows at face value rather than
            # pretending their injections were prefix-cached.
            c.execute("UPDATE events SET eff_overhead=dict_overhead")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    def record(self, report, direction: str = "in", provider: str = "",
               project: str = "") -> None:
        reps = ",".join(sorted({m.representation for m in report.messages})) or "passthrough"
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO events(ts, session, model, profile, exact,"
                " original_tokens, encoded_tokens, dict_overhead, saved, pct,"
                " latency_ms, fallback, representation, direction, provider,"
                " project, fallback_reason, eff_overhead)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), report.session_id, report.model, report.profile,
                 int(report.exact_tokenizer), report.original_tokens,
                 report.encoded_tokens, report.dictionary_overhead,
                 report.saved, report.pct, report.latency_ms,
                 int(report.fallback), reps, direction, provider, project,
                 getattr(report, "fallback_reason", ""),
                 getattr(report, "effective_overhead", 0)))

    # -- encode cache ----------------------------------------------------
    def cache_get(self, key: str) -> Optional[tuple[str, str]]:
        with self._conn() as c:
            row = c.execute("SELECT encoded, representation FROM enc_cache "
                            "WHERE key=?", (key,)).fetchone()
            return (row["encoded"], row["representation"]) if row else None

    def cache_put(self, key: str, encoded: str, representation: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT OR REPLACE INTO enc_cache VALUES(?,?,?,?)",
                      (key, encoded, representation, time.time()))

    # -- reporting -------------------------------------------------------
    def summary(self, since: float | None = None) -> dict:
        t0 = since or 0.0
        with self._conn() as c:
            row = c.execute("""
              SELECT COUNT(*) n, COALESCE(SUM(original_tokens),0) orig,
                     COALESCE(SUM(encoded_tokens),0) enc,
                     COALESCE(SUM(dict_overhead),0) overhead,
                     COALESCE(SUM(eff_overhead),0) overhead_effective,
                     COALESCE(SUM(saved),0) saved,
                     COALESCE(SUM(original_tokens-encoded_tokens-eff_overhead),0)
                        saved_effective,
                     COALESCE(AVG(latency_ms),0) avg_latency,
                     COALESCE(AVG(fallback)*100,0) fallback_pct,
                     COALESCE(AVG(fallback_reason LIKE 'error:%')*100,0) error_pct,
                     COALESCE(AVG(fallback_reason='not-worth-it')*100,0)
                        reverted_pct
              FROM events WHERE direction='in' AND ts>=?""", (t0,)).fetchone()
            out_row = c.execute("""
              SELECT COUNT(*) n, COALESCE(SUM(encoded_tokens),0) generated,
                     COALESCE(SUM(original_tokens),0) expanded,
                     COALESCE(SUM(original_tokens)-SUM(encoded_tokens),0) saved
              FROM events WHERE direction='out' AND ts>=?""", (t0,)).fetchone()
            by_model = [dict(r) for r in c.execute("""
              SELECT model, COUNT(*) n, SUM(saved) saved,
                     ROUND(AVG(pct),1) avg_pct
              FROM events WHERE direction='in' AND ts>=?
              GROUP BY model ORDER BY saved DESC""", (t0,))]
            by_rep = [dict(r) for r in c.execute("""
              SELECT representation, COUNT(*) n, SUM(saved) saved
              FROM events WHERE direction='in' AND ts>=?
              GROUP BY representation ORDER BY saved DESC""", (t0,))]
        d = dict(row)
        d["reduction_pct"] = round(d["saved"] / d["orig"] * 100, 2) if d["orig"] else 0.0
        # what compression really costs once byte-identical injection blocks
        # amortize under provider prefix caching
        d["reduction_pct_effective"] = (
            round(d["saved_effective"] / d["orig"] * 100, 2) if d["orig"] else 0.0)
        d["by_model"] = by_model
        d["by_representation"] = by_rep
        d["output"] = dict(out_row)   # generated vs locally-expanded tokens
        return d

    def dump_json(self) -> str:
        return json.dumps(self.summary(), indent=1)
