# FoldLang v1 specification

FoldLang is TokenFold's machine-facing intermediate representation. Phase 1
benchmarking (docs/phase1-findings.md) showed symbolic notations barely beat
terse English while adding ambiguity, and Chinese never wins on token count.
FoldLang v1 is therefore defined as:

> **Terse imperative English + alias codes, with protected regions untouched.**

## Grammar

- Clauses separated by `;` (1 token everywhere). Verb-first imperatives.
- Articles, politeness, and discourse filler removed (`please`, `I'd like
  you to`, `as always`...). Deterministic rule table: core/phrases.py.
- Comparators normalized: `at least 3` → `>=3`, `at most 10` → `<=10`.
- Negations, modality words (must/should/may/optional/required), numbers,
  dates, units are NEVER transformed (verifier-enforced invariants).
- Protected regions (code, paths, URLs, keys, JSON, quotes, identifiers...)
  pass through byte-exact.

## Alias codes

| Prefix | Meaning | Scope | Example |
|--------|---------|-------|---------|
| `K<n>` | instruction/concept | dictionary generation | `K3` = do not break existing functionality |
| `P<n>` | policy bundle | dictionary generation | `P1` = standing rules: K3+K4+K5+K6+K7 |
| `E<n>` | named entity | session | `E1` = Agent Manager |

A code may only be transmitted if its definition is established in the
model's current context, either by this session's earlier turns or by the
generation bootstrap block:

```
DICT v3 (codes below are shorthand; expand mentally, reply in plain terse
English unless told otherwise): K1=...; K2=...; ...
```

Bootstrap blocks are **byte-stable per generation** so provider prefix
caching amortizes their cost. New aliases accumulate in a nursery and are
frozen into generation N+1 only when
`net = (saved_per_use x expected_uses) - definition_cost - cache_miss_penalty > 0`.

## Response side

Human Mode: the model is asked for terse English; alias codes it emits are
expanded locally (token-free) by a streaming decoder. Unknown codes are left
verbatim, never invented. Agent Mode: responses stay compact for the next
agent; no expansion.

## Fold references

Long history is folded locally:

- `[code:<hash16> <n>L]` — a code fence stored in the session blob store
- `[ref:<hash16> <kind> unchanged from turn N]` — exact repeat of earlier content
- `[+N lines folded]` — extractive fold marker

The originals stay on disk (sessions dir) and can be re-expanded on demand.
