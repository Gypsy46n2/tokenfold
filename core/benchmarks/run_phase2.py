"""Phase 2 benchmark: the full pipeline on a realistic multi-turn agent
session, measuring cumulative context savings turn by turn.

Simulates a coding-agent conversation with your Agent Manager shape:
repeated boilerplate every turn, growing history, repeated file content,
long assistant answers. Measures original vs encoded context per turn on
each exact tokenizer profile.

Usage: python benchmarks/run_phase2.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TOKENFOLD_HOME",
                      tempfile.mkdtemp(prefix="tokenfold-bench-"))

from tokenfold.core.config import Config              # noqa: E402
from tokenfold.core.encoder import Encoder            # noqa: E402
from tokenfold.tokenizers.registry import profile_for  # noqa: E402

BOILER = ("As always, remember the standing rules for this project: do not "
          "break existing functionality, run the tests before finishing, "
          "preserve all public APIs, update the documentation whenever "
          "behavior changes, and never modify files that are unrelated to "
          "the current task. ")

FILE_BLOB = "```python\n" + "\n".join(
    f"def handler_{i}(req):\n    return process(req, mode={i})"
    for i in range(30)) + "\n```"

LONG_ANSWER = ("I looked into the Agent Manager scheduler. " +
               " ".join(f"Detail sentence {i} about the internal queue and "
                        f"how the workers coordinate." for i in range(25)) +
               " error: the retry loop never backs off. Decision: add "
               "exponential backoff capped at 60 s.")

TURNS = [
    ("user", BOILER + "Please analyze the scheduler module of the Agent "
     "Manager application and explain how jobs are dispatched.\n" + FILE_BLOB),
    ("assistant", LONG_ANSWER),
    ("user", BOILER + "Great. Now please fix the retry loop bug that you "
     "found in the Agent Manager scheduler, and make sure that you preserve "
     "the existing functionality.\n" + FILE_BLOB),
    ("assistant", LONG_ANSWER + " Applied the fix in scheduler.py."),
    ("user", BOILER + "Please add unit tests for the new backoff behavior "
     "in the Agent Manager scheduler, with at least 5 test cases."),
    ("assistant", "Added 6 tests covering backoff growth, cap, reset, "
     "jitter, zero-delay and overflow. All pass."),
    ("user", BOILER + "Now update the documentation of the Agent Manager "
     "scheduler to describe the backoff behavior, and do not modify "
     "unrelated files."),
    ("assistant", "Updated scheduler.md with a backoff section, examples "
     "included. Docs build passes."),
    ("user", BOILER + "Please run the full test suite for the Agent Manager "
     "one more time and report any failures."),
    ("assistant", "All 84 tests pass in 12.3 s. No failures."),
    ("user", BOILER + "Great. Finally, prepare a short summary of every "
     "change made in this session for the changelog."),
]


def run(profile_model: str, mode: str = "BALANCED") -> list[dict]:
    os.environ["TOKENFOLD_HOME"] = tempfile.mkdtemp(prefix="tf-run-")
    prof = profile_for(profile_model)
    cfg = Config(mode=mode, fold_history=True, fold_after_turns=2,
                 inject_terse_style=True, inject_bootstrap=True)
    enc = Encoder(cfg)
    from tokenfold.core.seed import seed
    seed(enc.dict)
    sys_msg = {"role": "system", "content": "You are Agent Manager's coding agent."}
    history: list[dict] = [sys_msg]
    rows = []
    for role, text in TURNS:
        history.append({"role": role, "content": text})
        if role != "user":
            continue
        out, rep = enc.encode(history, profile_model)
        turn_no = len([m for m in history if m["role"] == "user"])
        # effective: byte-stable injection blocks are prefix-cached after
        # their first send (Ollama KV: free; OpenAI/Claude: ~90% off)
        eff = rep.encoded_tokens + (
            rep.dictionary_overhead if turn_no == 1
            else round(rep.dictionary_overhead * 0.1))
        rows.append({
            "turn": turn_no,
            "original": rep.original_tokens,
            "encoded": rep.encoded_tokens + rep.dictionary_overhead,
            "effective": eff,
            "saved": rep.saved,
            "pct": round(rep.pct, 1),
            "eff_pct": round((rep.original_tokens - eff) / rep.original_tokens * 100, 1),
            "fallback": rep.fallback,
            "latency_ms": round(rep.latency_ms, 1),
        })
    return rows


def main() -> None:
    results = {}
    import itertools
    for model, mode in itertools.product(
            ("gpt-4o", "qwen2.5:0.5b"), ("BALANCED", "MAX")):
        rows = run(model, mode)
        model = f"{model}/{mode}"
        results[model] = rows
        tot_o = sum(r["original"] for r in rows)
        tot_e = sum(r["encoded"] for r in rows)
        print(f"\n=== {model} (profile {profile_for(model).name}) ===")
        tot_f = sum(r["effective"] for r in rows)
        print(f"{'turn':>4} {'orig':>7} {'enc':>7} {'pct':>6} {'eff':>7} {'effpct':>7} {'ms':>6}")
        for r in rows:
            print(f"{r['turn']:>4} {r['original']:>7} {r['encoded']:>7} "
                  f"{r['pct']:>5}% {r['effective']:>7} {r['eff_pct']:>6}% {r['latency_ms']:>6}")
        print(f"TOTAL {tot_o:>6} {tot_e:>7} {(tot_o - tot_e) / tot_o * 100:>5.1f}% "
              f"{tot_f:>7} {(tot_o - tot_f) / tot_o * 100:>6.1f}%")

    out = ROOT / "benchmarks" / "results" / "phase2.json"
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
