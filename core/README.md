# TokenFold

Transparent token-compression middleware for LLM communication.
You type normal English → the model receives the cheapest reliable
representation → you read normal English back.

```
User → TokenFold Encoder → AI Model → TokenFold Decoder → User
```

Measured on a realistic multi-turn agent session (benchmarks/results/
phase2.json; default MAX mode; all tokenizers agree within ~2 points):
**75% raw / 79% cache-effective total context reduction**, with steady-state
turns at **81–87% raw and 85–91% cache-effective** ("effective" credits the
prefix-cache discount providers give byte-stable injection blocks after
their first send). Turn 1 is 0% by design: a novel code payload is
information the model needs and is never compressed. Encode latency
4–20 ms. The rolling-digest engine replaces ALL old turns with one compact
state block (originals kept on disk, expandable on request); the seeded
alias library (tokenfold/core/seed.py) compresses standard agent boilerplate
from the first session. Output-side savings from the terse-style instruction
are additional and uncounted.

## What it actually does (in savings order)

1. **Instruction dedup + history folding** — old turns collapse to key-line
   extracts; repeated file/tool content becomes `[ref:...]` pointers;
   originals kept locally for re-expansion.
2. **Alias dictionary (FoldLang)** — recurring instructions become `K7`-style
   codes once their net savings (minus bootstrap and cache-miss cost) is
   positive. Generational, byte-stable, prefix-cache friendly. See
   docs/foldlang.md.
3. **Deterministic terse-English compression** — filler removal + verbose→terse
   rewrites (docs/phase1-findings.md: terse English ≈ 43% of raw tokens;
   Chinese was benchmarked and *never* wins, so it is not used).
4. **Output-side trimming** — a stable "reply tersely" style instruction
   (output tokens cost 3–5× input on paid APIs).

Safety: code/paths/URLs/keys/quotes/JSON are lifted out before compression
and restored byte-exact; an invariant verifier (negations, numbers,
comparators, modality) rejects any candidate that shifts meaning; **every
failure path falls back to the original message**.

## Quick start (Windows & Linux)

```
cd tokenfold
python -m pip install -e .        # or: pip install tiktoken tokenizers httpx fastapi uvicorn
python -m tokenfold.cli serve --port 9339 --upstream http://localhost:11434/v1
```

Point any OpenAI-compatible client (Agent Manager, LM Studio, scripts,
Open WebUI) at `http://localhost:9339/v1`. Anthropic clients: use
`http://localhost:9339/v1/messages` (auth passes through untouched).

- Dashboard: <http://localhost:9339/tokenfold/dashboard>
- Stats JSON: `python -m tokenfold.cli stats`
- One-shot preview: `python -m tokenfold.cli encode "Please analyze..." --model gpt-4o`
- Dictionary: `python -m tokenfold.cli dict list|mint|export|import FILE`
- Config: `python -m tokenfold.cli config set mode FAST|BALANCED|MAX|OFF`

Per-request overrides (headers): `X-TokenFold-Upstream`, `X-TokenFold-Mode`,
`X-TokenFold-Route: human|agent` (agent = responses stay compact for
agent-to-agent pipelines).

## Layout

```
tokenfold/core/        protected.py phrases.py dictionary.py verifier.py
                       encoder.py decoder.py folding.py session.py
                       metrics.py config.py
tokenfold/tokenizers/  registry.py     (o200k, cl100k, qwen, llama, deepseek,
                                        mistral, gemma exact; claude estimated)
tokenfold/adapters/    proxy.py        (OpenAI + Anthropic schemas, SSE)
claude-plugin/         Claude Code plugin (skill + optional PreToolUse hook)
benchmarks/            phase1 (representations) & phase2 (full pipeline)
tests/                 22 safety + pipeline tests
```

Data lives in `%LOCALAPPDATA%\tokenfold` (Windows) / XDG dirs (Linux);
override with `TOKENFOLD_HOME`. Only counts and hashes are logged — never
message content or protected values.

## Quality proof

`python benchmarks/run_quality.py` runs an objective task suite (cross-turn
fact recall, constraint memory, decision memory, code-name recall,
alias comprehension) through a real local model in OFF vs MAX. Current
result: **6/6 in both conditions** — compression changed no answers. If the
model loses something it needs, it can ask to expand any `[code:/ref:]`
reference: the proxy auto-injects the original and retries once
(non-streaming), or ships it on the next turn (streaming).

## Operating modes

| Mode | Behavior |
|------|----------|
| FAST | dictionary + deterministic compression only |
| BALANCED | + tokenizer-benchmarked candidate selection (default) |
| MAX | + background abstractive history folding via a tiny local Ollama model |
| OFF | byte-transparent passthrough |

## Design principle

`Total cost = bootstrap + input + output + translation overhead + latency`.
Every alias is minted only when net savings are positive after dictionary
overhead **and** a prefix-cache-miss penalty; candidates that don't clear a
minimum savings threshold ship as the original. When in doubt, passthrough.
