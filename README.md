# TokenFold

[![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-support-yellow?logo=buymeacoffee&logoColor=black)](https://buymeacoffee.com/Gypsy46n2)

**Transparent token-compression middleware for LLM communication.**

TokenFold is a local reverse proxy that sits between any chat client or
agent framework and any OpenAI- or Anthropic-compatible backend. You write
normal English; the model receives a verified, token-minimal encoding; the
model's reply is expanded back to normal English locally, at zero token
cost. Nothing about your workflow changes — only the size of what goes
over the wire.

```
You / your agent ──► TokenFold Encoder ──► AI model
                                              │
You / your agent ◄── TokenFold Decoder ◄──────┘
        (plain English)      (local, token-free expansion)
```

**Measured results** (real tokenizer counts — o200k, cl100k, Qwen, Llama,
DeepSeek, Mistral, Gemma — never character estimates):

| Metric | Result |
|---|---|
| Steady-state context reduction (multi-turn agent session) | **81–87% raw, 85–91% cache-effective** |
| Whole-session reduction incl. first sends | **75% raw / 79% cache-effective** |
| Single one-shot prompt | 25–40% |
| Encode latency | 4–20 ms, deterministic, no LLM in the loop |
| Answer-quality degradation (objective 6-task suite, OFF vs MAX) | **0 — 6/6 identical passes** |

"Cache-effective" credits the prefix-cache discount providers grant
byte-stable blocks after first send (Ollama KV cache: free; OpenAI:
automatic; Anthropic: via `cache_control`, which TokenFold sets for you).

---

## Why this exists

LLM conversations are structurally wasteful, and agents make it worse:

1. **Stateless APIs re-send everything.** Every request retransmits the
   entire conversation. By turn 10 you have paid for turns 1–9 nine times.
2. **Agents repeat boilerplate.** "Don't break existing functionality, run
   the tests, preserve public APIs..." — 40–50 identical tokens, every
   single turn.
3. **Content repeats byte-for-byte.** Pasted files and tool outputs get
   re-sent unchanged, turn after turn.
4. **Natural English is padded.** Politeness and filler cost real tokens
   ("I would like you to please go ahead and..." → "do X").

TokenFold attacks all four, locally and deterministically.

## How it works

### 1. Rolling digest — history stops being re-sent
The largest lever. Only the newest exchange stays verbatim. Everything
older is collapsed into **one compact `HIST:` block**: key lines only
(decisions, errors, constraints, numbers), selected by deterministic
scoring, terse-compressed, deduplicated, and capped in size regardless of
session length. A 2,000-token history becomes ~100 tokens.

Nothing is lost: originals are stored on disk, every fold carries a
content hash, and if the model needs folded content it asks — the proxy
detects the request, injects the original, and retries once before you
even see the answer (streaming responses queue the expansion for the next
turn instead).

### 2. Content references — anything repeated is sent once
Code fences and bulky tool results become stable references:

```
[code:eff57a2f5842e4e7 61L]        ← was a 450-token file, re-sent 6×
[ref:9a01... tool-result]          ← was a 3,000-token grep dump
```

First send is full; every repeat costs ~10 tokens. Tool messages keep
their `tool_call_id` pairing so API validation never breaks.

### 3. Alias dictionary (FoldLang)
Recurring instructions compress to codes, established by a compact,
byte-stable `DICT` block that rides along on every request that uses them:

```
DICT v1 (shorthand; expand mentally): P1=K3+K4+K5+K6+K7;
K3=do not break existing behavior; K4=run the full test suite after changes; ...
```

Your 47-token standing-rules paragraph travels as **`P1` — 2 tokens**.
A seeded library covers standard agent boilerplate out of the box; a
learning system watches real traffic and promotes new aliases **only when
`(tokens_saved × expected_reuse) − definition_cost − cache_miss_penalty > 0`**.
The model may also *reply* using codes — the decoder expands them locally,
making those output tokens free (output tokens cost 3–5× input on paid APIs).

### 4. Deterministic terse-English rewriting
A rule table strips filler and tightens verbose constructions
("in the event that" → "if", "at least 3" → ">=3"). Benchmarked across
seven tokenizers: terse English costs **~43%** of natural English — and
beats Simplified Chinese on *every* vocabulary tested, including Qwen and
DeepSeek (see `core/docs/phase1-findings.md`; the "Chinese is denser"
intuition is about characters, not tokens).

### 5. Total-cost decision, per request
The encoder builds two complete candidate encodings — with aliases (+
dictionary overhead) and without — counts both with the *destination
model's actual tokenizer*, prices injection blocks at their cache-aware
effective cost, and ships whichever total is cheaper. One-shot requests
skip the dictionary; multi-turn sessions invest in it and amortize.

### What a request actually looks like on the wire

Turn 6 of an agent session — original 2,147 tokens, sent as 398:

```
[system]  You are Agent Manager's coding agent.
          DICT v1 (shorthand; expand mentally): P1=K3+K4+K5+K6+K7; K3=...; ...
          ENT: E1=Agent Manager
          Reply terse, no preamble/summary. Code/paths/numbers exact.
[system]  HIST (earlier turns, compacted; refs expandable on request):
          u1: standing rules: P1. | u1: [code:eff57a2 61L] |
          a2: error: the retry loop never backs off. |
          a2: Decision: add exponential backoff capped at 60 s. | ...
[user]    the standing rules for this project: P1. Prepare a short summary
          of every change made in this session for the changelog.
```

## Safety model

Four guarantees run on **every** request:

1. **Protected regions are byte-exact.** Code, paths, URLs, keys, hashes,
   UUIDs, numbers+units, dates, JSON/XML/YAML, quotes, identifiers — lifted
   out before compression, restored identically. Never rewritten.
2. **A semantic verifier diffs invariants.** Every candidate is checked
   against the original for negations ("do not delete" can never become
   "delete"), numbers, comparators, and must/may/optional modality. Any
   mismatch kills the candidate.
3. **Never larger.** The assembled request is guaranteed ≤ the original,
   or the original ships untouched.
4. **Fail-soft everywhere.** Any error in any pipeline stage → original
   message passes through. TokenFold cannot block traffic.

Proof, not promise: `core/benchmarks/run_quality.py` replays an objective
task suite (cross-turn fact recall, constraint memory, decision memory,
code-name recall, alias comprehension) through a real local model with
compression OFF vs MAX and compares answers. Current result: **6/6
identical in both conditions.** Re-run it yourself against your own model.

## Quick start

**Windows**
```powershell
cd windows
powershell -ExecutionPolicy Bypass -File install.ps1
.\start.ps1          # or .\start-background.ps1 / .\autostart.ps1
```

**Linux**
```bash
cd linux
./install.sh
./start.sh           # or ./start-background.sh; systemd unit included
```

**Integrate** — point any OpenAI-compatible client's base URL at:
```
http://localhost:9339/v1
```
Anthropic clients: `http://localhost:9339/v1/messages`. API keys pass
through untouched; TokenFold never reads, stores, or logs them. Default
upstream is Ollama (`http://localhost:11434/v1`); change it:
```bash
python -m tokenfold.cli config set upstream http://localhost:1234/v1
```

**Observe**
- Dashboard: `http://localhost:9339/tokenfold/dashboard` — lifetime tokens
  saved, per-model and per-representation breakdown, fallback rate,
  output-side savings.
- CLI: `tokenfold stats`, `tokenfold dict list`,
  `tokenfold encode "prompt" --model gpt-4o` (previews any compression).

Full end-user documentation: **[USER-GUIDE.md](USER-GUIDE.md)**.

## Configuration

| Mode | Behavior |
|---|---|
| `MAX` *(default)* | Full folding: 1-turn verbatim window, rolling digest, aliases, background compaction |
| `BALANCED` | Same engines, wider verbatim window |
| `FAST` | Dictionary + deterministic compression only |
| `OFF` | Byte-transparent passthrough |

Per-request header overrides: `X-TokenFold-Mode`, `X-TokenFold-Route`
(`human` = readable replies; `agent` = replies stay compact for
agent→agent pipelines), `X-TokenFold-Upstream`, `X-TokenFold-Session`.

Key settings (`tokenfold config set KEY VALUE`): `fold_after_turns`,
`min_confidence`, `scope` (per-project dictionaries), `tiny_model` +
`abstractive_threshold_tokens` (optional background summarizer via a small
Ollama model — never on the request path).

## Repository layout

```
USER-GUIDE.md      end-user documentation: use cases, setup, how savings work
windows/           installer + start/stop/autostart scripts (PowerShell)
linux/             installer + start/stop scripts + systemd user unit
core/              the engine
  tokenfold/core/        protected.py phrases.py dictionary.py verifier.py
                         encoder.py decoder.py folding.py session.py metrics.py
  tokenfold/tokenizers/  per-model tokenizer registry (7 exact vocabularies)
  tokenfold/adapters/    OpenAI + Anthropic proxy (SSE streaming, auto-expansion)
  tests/                 27 safety + pipeline tests
  benchmarks/            phase1 (representations), phase2 (full pipeline),
                         run_quality.py (degradation harness) + results/
  docs/                  foldlang.md spec, phase1-findings.md (measured findings)
claude-plugin/     Claude Code plugin (skill + optional PreToolUse hook)
```

## Benchmarks — reproduce everything

```bash
cd core
python -m pytest tests -q            # 27 tests
python benchmarks/run_phase1.py      # 7 representations × 7 tokenizers
python benchmarks/run_phase2.py      # full pipeline, turn-by-turn savings
python benchmarks/run_quality.py     # OFF-vs-MAX answer comparison (needs Ollama)
```

Design principle behind every decision:
`total cost = dictionary bootstrap + input + output + translation overhead + latency`.
A representation that saves 100 input tokens but costs 500 output tokens is
a regression; anything unproven ships as the original. When in doubt,
passthrough.

## Privacy

All processing is local. Message content goes only to the backend *you*
configure. Metrics store counts, hashes, and latencies — never text, never
credentials. State lives in `%LOCALAPPDATA%\tokenfold` (Windows) /
`~/.local/share/tokenfold` (Linux); delete it to reset.

## Support

TokenFold is free and open source. If it saves you tokens (and money),
you can support development here:

**☕ [buymeacoffee.com/Gypsy46n2](https://buymeacoffee.com/Gypsy46n2)**

## Status

v0.1.0 — working proxy, verified against Ollama (Qwen/Gemma family) with
exact-tokenizer benchmarks for GPT/Qwen/Llama/DeepSeek/Mistral/Gemma and
estimated counts for Claude (exact via `count_tokens` when a key is
configured). Open source under the [MIT License](LICENSE).
