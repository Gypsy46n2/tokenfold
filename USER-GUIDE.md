# TokenFold — User Guide

**Work naturally in plain English. TokenFold quietly shrinks what actually
gets sent to the AI — and expands what comes back — so every conversation
costs a fraction of the tokens, context, and money it would otherwise.**

```
You  →  TokenFold Encoder  →  AI model  →  TokenFold Decoder  →  You
        (compresses)                        (expands, token-free)
```

You never see the compressed form. You type normal sentences; you read
normal sentences. Everything in between is optimized.

---

## 1. What is it for? (the use case)

TokenFold is a local proxy that sits between any chat client or agent and
any OpenAI-compatible AI backend (Ollama, LM Studio, llama.cpp, OpenAI,
Anthropic, Agent Manager, custom agents...). It exists because real AI
conversations are astonishingly wasteful:

- **Agents repeat themselves.** "Don't break existing functionality, run
  the tests, preserve the APIs, update the docs..." gets re-sent on every
  single turn — often 40–50 tokens of identical boilerplate, dozens of
  times per session.
- **History piles up.** Every request re-sends the entire conversation.
  By turn 10, you're paying for turns 1–9 all over again, even though the
  model already processed them.
- **The same content gets re-sent.** A pasted file, a long tool output —
  transmitted again and again, byte for byte.
- **Polite English is expensive.** "I would like you to please go ahead
  and..." costs tokens and adds nothing.

Typical wins, measured with real tokenizers (not estimates):

| Situation | Savings |
|---|---|
| Long agent session, steady state | **81–87%** of input tokens (85–91% counting provider prefix-cache discounts) |
| Whole multi-turn session including first sends | **~75%** |
| A single one-off prompt | 25–40% |
| Brand-new content (pasted code, new specs) | 0% — deliberately; see §4 |

That translates directly into: lower API bills, more room in the context
window before things get truncated, less KV-cache load on a local GPU, and
longer sessions before quality degrades.

---

## 2. Quick start

### Windows
```
cd windows
powershell -ExecutionPolicy Bypass -File install.ps1
.\start.ps1                     # or .\start-background.ps1
```
Optional: `.\autostart.ps1` registers it to start at logon.

### Linux
```
cd linux
./install.sh
./start.sh                      # or ./start-background.sh
```
Optional: `tokenfold.service` is a ready systemd user unit (instructions
inside the file).

### Point your client at it
Wherever your app asks for an OpenAI-compatible **base URL**, use:

```
http://localhost:9339/v1
```

instead of the backend's own URL. That's the entire integration. TokenFold
forwards to Ollama (`http://localhost:11434/v1`) by default; change the
upstream with:

```
python -m tokenfold.cli config set upstream http://localhost:1234/v1
```

or per-request with the `X-TokenFold-Upstream` header. Anthropic clients
use `http://localhost:9339/v1/messages`; API keys pass through untouched —
TokenFold never reads or stores them.

### Watch it work
- Live dashboard: **http://localhost:9339/tokenfold/dashboard** — lifetime
  tokens saved, per-model breakdown, fallback rate.
- Terminal: `python -m tokenfold.cli stats`
- Preview any single prompt's compression:
  `python -m tokenfold.cli encode "your prompt here" --model gpt-4o`

---

## 3. How the folding tech saves tokens

Everything below happens **locally, deterministically, in 4–20 ms** — no
cloud calls, no extra AI needed to save tokens for the main AI.

### a) Rolling digest — old turns stop being re-sent
The single biggest lever. Instead of re-transmitting the whole
conversation every request, TokenFold keeps only the newest exchange
verbatim and collapses everything older into **one compact `HIST:` block**:
the key lines only — decisions, errors, constraints, numbers — extracted by
rules, deduplicated, and capped in size no matter how long the session
gets. A 2,000-token history becomes a ~100-token digest.

Nothing is destroyed: the original turns are stored on disk. If the model
needs something that was folded away, it asks ("expand [code:...]"), and
the proxy automatically re-injects the original — retrying once behind the
scenes before you even see the answer.

### b) References — repeated content is sent once
Code fences and bulky tool outputs become short references like
`[code:a1b2c3... 61L]`. The first send is full; every repeat costs ~10
tokens. Same for any content block that appears twice.

### c) The alias dictionary (FoldLang)
Recurring instructions become short codes established once per request in
a compact, byte-stable `DICT` block:

```
K3 = do not break existing behavior      P1 = K3+K4+K5+K6+K7
K4 = run the full test suite after changes    ...
```

Your 47-token standing-rules paragraph travels as **`P1` — 2 tokens**. A
starter library covers standard agent boilerplate out of the box, and a
learning system watches your real traffic, spotting phrases you repeat and
promoting them to new codes **only when the math says the alias saves more
than its definition costs** (including the cache-miss penalty of changing
the dictionary). The model can also *reply* using codes — your decoder
expands them locally, which makes those output tokens literally free.

### d) Terse-English compression
Deterministic rewrite rules strip filler ("please", "I'd like you to",
"go ahead and") and tighten verbose constructions ("in the event that" →
"if", "at least 3" → ">=3"). Measured across seven tokenizers: terse
English is ~43% the cost of natural English, and it beats Simplified
Chinese on *every* vocabulary we tested — so TokenFold uses terse English,
not translation. On the way back, a style instruction keeps model replies
terse too (output tokens cost 3–5× input on paid APIs).

### e) Cache-aware accounting
The injected `DICT`/`HIST` blocks are byte-stable, which means provider
prefix caches (Ollama's KV cache, OpenAI automatic caching, Anthropic
cache_control — which TokenFold sets for you) make them nearly free after
the first send. TokenFold's decisions account for this: it will invest a
one-time dictionary send when the session will amortize it, and skip it
for one-shot requests.

### Why it's safe to work naturally

The reason you can trust the folding is that four hard guarantees run on
every single request:

1. **Protected regions are byte-exact.** Code, file paths, URLs, API keys,
   numbers with units, dates, JSON, quotes, identifiers — lifted out before
   any compression and restored identically. They are never rewritten.
2. **An invariant verifier compares meaning.** Every compressed candidate
   is checked against the original for negations ("do not delete" can
   never become "delete"), numbers, comparators, and must/may/optional
   modality. Any mismatch kills the candidate.
3. **Never larger.** The assembled request is mathematically guaranteed to
   be ≤ the original, or TokenFold sends the original untouched.
4. **Fail-soft everywhere.** Any error, anywhere in the pipeline, results
   in your original message passing straight through. TokenFold can never
   block you from talking to your AI.

And it's proven, not just promised: `python core/benchmarks/run_quality.py`
runs an objective task suite (cross-turn fact recall, constraint memory,
decision memory, code recall) through a real local model with compression
OFF vs MAX. Current result: **6/6 identical passes in both conditions.**

---

## 4. What TokenFold refuses to compress (and why that's a feature)

The first time you paste a file or write a genuinely new spec, TokenFold
sends it nearly untouched — you'll see 0% savings on that turn. That is
correct behavior: novel content is information the model *needs*, and
"compressing" it would mean silently withholding things from the model.
The savings come from everything around it: the boilerplate, the history,
the repeats. By turn 3 of a session, savings routinely exceed 85%.

---

## 5. Modes and configuration

| Mode | What it does |
|---|---|
| `MAX` *(default)* | Full folding: digest history, aliases, terse rewrite, background compaction |
| `BALANCED` | Same engines, gentler history window |
| `FAST` | Dictionary + deterministic compression only |
| `OFF` | Byte-transparent passthrough |

```
python -m tokenfold.cli config set mode BALANCED     # change default
python -m tokenfold.cli config get                    # see everything
python -m tokenfold.cli dict list                     # see the alias dictionary
```

Per-request overrides via headers: `X-TokenFold-Mode`, `X-TokenFold-Route`
(`human` = readable replies, `agent` = keep replies compact for
agent-to-agent pipelines), `X-TokenFold-Upstream`, `X-TokenFold-Session`.

Useful knobs (`config set KEY VALUE`): `fold_after_turns` (how many recent
turns stay verbatim), `min_confidence` (semantic safety floor),
`tiny_model` / `abstractive_threshold_tokens` (background summarizer),
`scope` (per-project dictionaries).

## 6. Privacy

Everything runs locally. No message content ever leaves your machine
except to the AI backend *you* configured. Metrics store only counts,
hashes, and latencies — never text, never keys. Session/dictionary data
lives in `%LOCALAPPDATA%\tokenfold` (Windows) or `~/.local/share/tokenfold`
(Linux); delete those folders to reset everything.

## 7. FAQ

**Does the model get confused by the codes?** The definitions ride along
in every request that uses them, in a compact block the model reads first.
The quality suite shows models follow them correctly. A model that ignores
them still sees the definitions, so meaning is never lost.

**What if the model needs something that was folded away?** It asks, and
the proxy expands automatically (one transparent retry). You can also just
re-paste anything at any time.

**Does it work with streaming?** Yes — responses stream through with
codes expanded on the fly; you lose nothing.

**Can two different apps share it?** Yes. Sessions are keyed per
conversation; use the `X-TokenFold-Session` header for explicit keying.

**How do I uninstall?** Stop the proxy, delete this folder and the data
folder in §6. Nothing else is touched.
