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

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    def record(self, report, direction: str = "in", provider: str = "",
               project: str = "") -> None:
        reps = ",".join(sorted({m.representation for m in report.messages})) or "passthrough"
        with self._lock, self._conn() as c:
            c.execute(
                "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), report.session_id, report.model, report.profile,
                 int(report.exact_tokenizer), report.original_tokens,
                 report.encoded_tokens, report.dictionary_overhead,
                 report.saved, report.pct, report.latency_ms,
                 int(report.fallback), reps, direction, provider, project))

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
    def summary(self) -> dict:
        with self._conn() as c:
            row = c.execute("""
              SELECT COUNT(*) n, COALESCE(SUM(original_tokens),0) orig,
                     COALESCE(SUM(encoded_tokens),0) enc,
                     COALESCE(SUM(dict_overhead),0) overhead,
                     COALESCE(SUM(saved),0) saved,
                     COALESCE(AVG(latency_ms),0) avg_latency,
                     COALESCE(AVG(fallback)*100,0) fallback_pct
              FROM events WHERE direction='in'""").fetchone()
            out_row = c.execute("""
              SELECT COUNT(*) n, COALESCE(SUM(encoded_tokens),0) generated,
                     COALESCE(SUM(original_tokens),0) expanded,
                     COALESCE(SUM(original_tokens)-SUM(encoded_tokens),0) saved
              FROM events WHERE direction='out'""").fetchone()
            by_model = [dict(r) for r in c.execute("""
              SELECT model, COUNT(*) n, SUM(saved) saved,
                     ROUND(AVG(pct),1) avg_pct
              FROM events WHERE direction='in'
              GROUP BY model ORDER BY saved DESC""")]
            by_rep = [dict(r) for r in c.execute("""
              SELECT representation, COUNT(*) n, SUM(saved) saved
              FROM events WHERE direction='in'
              GROUP BY representation ORDER BY saved DESC""")]
        d = dict(row)
        d["reduction_pct"] = round(d["saved"] / d["orig"] * 100, 2) if d["orig"] else 0.0
        d["by_model"] = by_model
        d["by_representation"] = by_rep
        d["output"] = dict(out_row)   # generated vs locally-expanded tokens
        return d

    def dump_json(self) -> str:
        return json.dumps(self.summary(), indent=1)
