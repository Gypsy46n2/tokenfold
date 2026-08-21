"""Phase 1 runner: measure every representation against every tokenizer.

Usage:  python benchmarks/run_phase1.py
Writes: benchmarks/results/phase1.json and prints a summary matrix.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from corpus.phase1 import CORPUS, BOOTSTRAP  # noqa: E402
from tokenfold.tokenizers.registry import all_profiles  # noqa: E402

REPS = ["raw_en", "concise_en", "terse_en", "raw_zh", "concise_zh",
        "foldlang_sym", "foldlang_alias"]


def main() -> None:
    profiles = all_profiles()
    results: dict = {"bootstrap_tokens": {}, "items": []}

    for pname, prof in profiles.items():
        results["bootstrap_tokens"][pname] = prof.count(BOOTSTRAP)

    for item in CORPUS:
        row = {"id": item["id"], "category": item["category"], "counts": {}}
        for rep in REPS:
            text = item["reps"][rep]
            row["counts"][rep] = {p: prof.count(text) for p, prof in profiles.items()}
        results["items"].append(row)

    out = ROOT / "benchmarks" / "results" / "phase1.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding="utf-8")

    # ---- summary: mean % of raw_en, per representation x tokenizer ----
    print(f"{'rep':16s}", *[f"{p:>10s}" for p in profiles], sep="")
    for rep in REPS:
        cells = []
        for p in profiles:
            tot_rep = sum(i["counts"][rep][p] for i in results["items"])
            tot_raw = sum(i["counts"]["raw_en"][p] for i in results["items"])
            cells.append(f"{tot_rep / tot_raw * 100:9.1f}%")
        print(f"{rep:16s}", *cells, sep="")

    print("\nbootstrap overhead (tokens):",
          {p: results['bootstrap_tokens'][p] for p in profiles})

    # per-category winner on o200k and qwen2.5 (if present)
    for p in ("o200k", "qwen2.5"):
        if p not in profiles:
            continue
        print(f"\nper-item winner on {p}:")
        for i in results["items"]:
            counts = {rep: i["counts"][rep][p] for rep in REPS}
            best = min(counts, key=counts.get)
            print(f"  {i['id']:22s} raw={counts['raw_en']:4d}  "
                  f"best={best} ({counts[best]}, "
                  f"{counts[best] / counts['raw_en'] * 100:.0f}%)")


if __name__ == "__main__":
    main()
