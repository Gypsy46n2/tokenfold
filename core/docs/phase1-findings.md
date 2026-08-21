# Phase 1 findings — tokenizer ground truth (2026-08-20)

Matrix: 8 tokenizer profiles (o200k, cl100k, claude-est*, qwen2.5, llama3,
deepseek3, mistral, gemma2) x 7 representations x 8 prompt categories.
Raw data: benchmarks/results/phase1.json. (*claude-est is a heuristic.)

## Results (total tokens as % of raw English)

| rep            | o200k | cl100k | qwen2.5 | llama3 | deepseek3 | mistral | gemma2 |
|----------------|-------|--------|---------|--------|-----------|---------|--------|
| concise_en     | 60.3  | 60.8   | 60.8    | 60.8   | 61.0      | 65.8    | 61.7   |
| terse_en       | 42.5  | 42.9   | 42.9    | 42.9   | 43.2      | 45.8    | 42.6   |
| raw_zh         | 101.4 | 142.7  | 85.6    | 105.7  | 79.2      | 138.9   | 85.3   |
| concise_zh     | 67.6  | 92.9   | 58.0    | 71.6   | 56.2      | 90.1    | 57.8   |
| foldlang_sym   | 40.2  | 40.1   | 40.1    | 40.1   | 42.0      | 42.8    | 41.5   |
| foldlang_alias | 27.9  | 28.0   | 28.4    | 28.0   | 28.1      | 29.0    | 28.3   |

Bootstrap dictionary overhead: 126-137 tokens (once per cached prefix).

## Conclusions baked into the design

1. **Chinese is dropped as a compression representation.** Even on the most
   CJK-friendly vocabularies (DeepSeek, Qwen, Gemma) concise Chinese (56-58%)
   loses to terse English (43%). On cl100k/Mistral it is a catastrophe (90%+).
   Translation would also require a model (compute) and risks meaning drift.
   Decoder still *understands* Chinese responses; we just never encode into it.
2. **FoldLang v1 = terse imperative English + alias codes.** Symbolic syntax
   gains only ~2-3 points over terse English and increases ambiguity risk.
   Grammar: semicolon-separated clauses, verb-first, articles/fillers dropped,
   arrows and ! only where they tokenize to 1 token.
3. **Aliases dominate: 28% overall, 4% on repeated boilerplate.** The
   dictionary (K/P/E codes) is the core savings engine. Bootstrap ~130 tokens
   amortizes via prefix caching; net-positive within 1-2 requests on
   boilerplate-heavy agent traffic.
4. **Savings are tokenizer-stable.** All exact profiles agree within ~3 points,
   so one FoldLang encoding serves all backends; per-model benchmarking is a
   selection safeguard, not a per-model rewrite.

## Phase 2 update (same day, after seeding + total-cost decision)

Full-pipeline benchmark (multi-turn agent session, MAX mode, o200k):
turn 1: 0% (code-dominated; correctly declined), turn 2: 31%,
turn 3: 64.6%, turn 4: **79.9%**; session total 55.7%. Key mechanisms in
order of contribution: fence->ref folding, sentence-level extractive folds,
seeded K/P aliases with subset bootstrap (grow-only order, P-bundles by
member reference), total-cost decision (alias+bootstrap vs terse-only per
request). Stateless-API rule: definition blocks ride on EVERY request that
uses codes; byte-stability makes them prefix-cache-cheap.

## Phase 3 update: rolling digest (same day)

MAX mode now replaces all old turns with ONE digest block (terse-compressed,
alias-passed, deduped key lines; scoped strictly to turns present in the
current request). Global invariant added: the assembled request is NEVER
larger than the original (passthrough otherwise). Cache-aware total-cost
decision: injection blocks are judged at ~10% of face value once byte-stable
in the session prefix (or when a session is proven multi-turn).

6-turn benchmark (gpt-4o/MAX): raw per-turn 0/52/87/84/83/82%,
cache-effective 0/58/91/88/87/85%; totals 75% raw, 79% effective.
Steady-state ~85-91% effective. The residual is the information floor:
system prompt, DICT subset, digest of genuinely-unique decisions, and the
novel content of the current message. Verified live: qwen3:8b answered a
turn-3 question that required facts available only via the digest.

## Phase 4 update: quality proof + output side + tools (2026-08-20)

Quality harness (benchmarks/run_quality.py, qwen3:8b, 6 objective tasks
incl. cross-turn fact/constraint/decision/code-name recall): **6/6 PASS in
both OFF and MAX** — no measured degradation at 85-91% compression.
(code_name recall survived via the model echoing the name into a digested
turn; the guaranteed path is auto re-expansion, below.)

New machinery:
- Auto re-expansion: model mentions "expand" + a [code:/ref:] hash ->
  non-streaming proxy injects the original and retries once before
  answering; streaming path queues it so the next request ships the
  original verbatim (pending_expansions, consumed once).
- Output-side: style instruction now permits DICT/ENT codes in replies
  (decoder expands token-free); response metrics recorded as direction=out
  (generated vs locally-expanded tokens) and shown separately in stats.
- Tool traffic: old role=tool results >200 chars become [ref:hash] while
  keeping the message for API pairing; expandable like any ref.
- Anthropic adapter marks the injected system head with cache_control so
  Claude actually prefix-caches it (the "effective" discount realized).
- X-TokenFold-Session header keys sessions explicitly (branch-safe).
- Background digest compaction: tiny model squeezes digest lines beyond
  2x the render window into <=4 dense lines (union hash scoping).
