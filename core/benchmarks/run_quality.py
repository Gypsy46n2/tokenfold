"""Quality/degradation harness: does compression change answers?

Runs a task suite through a real local model twice — mode OFF (verbatim)
and mode MAX (full compression) — and scores answers on objective checks
(expected substrings). Multi-turn tasks exercise the digest engine's memory:
facts stated in early turns must survive into late answers.

Usage: python benchmarks/run_quality.py [model]   (default qwen3:8b)
Writes benchmarks/results/quality.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MODEL = sys.argv[1] if len(sys.argv) > 1 else "qwen3:8b"
OLLAMA = "http://localhost:11434/v1/chat/completions"

BOILER = ("As always, remember the standing rules for this project: do not "
          "break existing functionality, run the tests before finishing, "
          "preserve all public APIs, update the documentation whenever "
          "behavior changes, and never modify files that are unrelated to "
          "the current task. ")

TASKS = [
    {
        "id": "fact_memory",
        "turns": [
            "Project codenamed Falcon has a budget of $8,400 and a deadline "
            "of 2026-11-15. Acknowledge with OK.",
            "The team hired 3 contractors at $700 each, paid from that "
            "budget. Acknowledge with OK.",
            "State the remaining budget after paying the contractors, and "
            "repeat the deadline exactly.",
        ],
        "expect": [["6,300", "6300"], ["2026-11-15"]],
    },
    {
        "id": "constraint_memory",
        "turns": [
            BOILER + "Additionally, for this task the one file you must "
            "never modify is config.yaml. Acknowledge with OK.",
            "Quick check: which single file must never be modified?",
        ],
        "expect": [["config.yaml"]],
    },
    {
        "id": "code_name_memory",
        "turns": [
            "Here is my function:\n```python\ndef calc_backoff_v3(attempt):\n"
            "    return min(2 ** attempt, 60)\n```\nAcknowledge with OK.",
            "What is the exact name of the function I sent earlier? If you "
            "cannot see it any more, ask to expand the code reference.",
        ],
        "expect": [["calc_backoff_v3"]],
    },
    {
        "id": "decision_memory",
        "turns": [
            "After evaluating storage options for the Agent Manager, here is "
            "the outcome. " + "We considered many alternatives in detail. " * 15 +
            "Decision: use PostgreSQL 16 with pgbouncer for connection "
            "pooling. Acknowledge with OK.",
            "Briefly: which database and which connection pooler did we decide on?",
        ],
        "expect": [["PostgreSQL", "postgres"], ["pgbouncer"]],
    },
    {
        "id": "alias_comprehension",
        "turns": [
            BOILER + "List the standing rules briefly as bullets.",
        ],
        "expect": [["test"], ["API"], ["doc"]],
    },
    {
        "id": "sanity_arith",
        "turns": ["What is 17 multiplied by 23? Give just the number."],
        "expect": [["391"]],
    },
]


def run_condition(mode: str) -> dict:
    os.environ["TOKENFOLD_HOME"] = tempfile.mkdtemp(prefix=f"tf-q-{mode}-")
    # fresh import state per condition not needed; Engine reads env dynamically
    from tokenfold.core.config import Config
    from tokenfold.engine import Engine
    import httpx

    eng = Engine(Config(mode=mode))
    results = {}
    for task in TASKS:
        hist: list[dict] = [{"role": "system",
                             "content": "You are Agent Manager's coding agent."}]
        answer = ""
        for user_text in task["turns"]:
            hist.append({"role": "user", "content": user_text})
            encoded, rep = eng.encode(hist, MODEL)
            r = httpx.post(OLLAMA, json={
                "model": MODEL, "messages": encoded, "stream": False,
                "options": {"temperature": 0}}, timeout=900)
            raw = r.json()["choices"][0]["message"]["content"]
            answer = eng.decode(raw, rep.session_id)
            hist.append({"role": "assistant", "content": answer})
        checks = [any(alt.lower() in answer.lower() for alt in group)
                  for group in task["expect"]]
        results[task["id"]] = {
            "pass": all(checks),
            "checks": checks,
            "answer_tail": answer[-300:],
        }
        print(f"  [{mode}] {task['id']}: {'PASS' if all(checks) else 'FAIL'} "
              f"{checks}", flush=True)
    return results


def main() -> None:
    print(f"model: {MODEL}")
    out = {}
    for mode in ("OFF", "MAX"):
        print(f"== condition {mode} ==", flush=True)
        out[mode] = run_condition(mode)
    n = len(TASKS)
    for mode in out:
        passed = sum(1 for r in out[mode].values() if r["pass"])
        print(f"{mode}: {passed}/{n} tasks pass")
    p = ROOT / "benchmarks" / "results" / "quality.json"
    p.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
