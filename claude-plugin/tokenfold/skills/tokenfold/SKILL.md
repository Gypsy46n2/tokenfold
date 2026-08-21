---
name: tokenfold
description: Compress prompts, context blocks, or subagent instructions into token-efficient FoldLang using the local TokenFold engine. Use when the user asks to compress a prompt, save tokens/context, check TokenFold savings stats, or manage the alias dictionary.
---

# TokenFold

TokenFold is a local token-compression engine installed at `D:\ChaTsAVER\tokenfold\core`
(cross-platform; install via ../windows/install.ps1 or ../linux/install.sh). It converts verbose
English into terse, alias-compressed FoldLang, verified to preserve semantics
(negations, numbers, modality, protected code/paths/URLs byte-exact).

## Commands

All commands work from any directory:

- Compress a prompt and see savings:
  `python -m tokenfold.cli encode "TEXT" --model gpt-4o`
  Returns JSON: encoded messages + original/encoded token counts + savings.
- Lifetime savings stats: `python -m tokenfold.cli stats`
- Dictionary: `python -m tokenfold.cli dict list|export|mint`
- Config: `python -m tokenfold.cli config get` / `config set KEY VALUE`
- Run proxy for any OpenAI-compatible backend:
  `python -m tokenfold.cli serve --port 9339 --upstream http://localhost:11434/v1`

## When compressing subagent prompts

1. Run `encode` with the target model id so the right tokenizer profile is used.
2. Use the `encoded` message content as the subagent prompt ONLY if `fallback`
   is false and `saved` is meaningful (>= ~20 tokens); otherwise use the original.
3. Never compress: code, file paths, URLs, exact quotes — the engine protects
   these automatically, but do not strip them yourself before encoding.

## Session dictionary

If the encoded text contains K/P/E codes, the definitions must already be in
the subagent's context: prepend the output of
`python -m tokenfold.cli dict export` bootstrap block, or avoid alias codes by
setting `config set inject_bootstrap false`.
