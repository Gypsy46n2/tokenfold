"""Unified tokenizer registry.

Every compression decision in TokenFold is made against the tokenizer of the
*destination* model. This module provides one API:

    count(text, model_id) -> int
    profile_for(model_id) -> TokenizerProfile

Profiles map model-id patterns (OpenAI, Claude, Qwen, Llama, DeepSeek,
Mistral, Gemma, Ollama tags...) onto concrete counters:

  * tiktoken encodings (exact for OpenAI models)
  * HuggingFace tokenizer.json files shipped in assets/ (exact for open models)
  * a calibrated heuristic estimator for Claude (Anthropic's tokenizer is not
    public; exact counts require the count_tokens API, pluggable when a key is
    configured)

All counters are cached; counting is microseconds after first load.
"""

from __future__ import annotations

import functools
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Bug (found live, agent-manager integration 2026-08-21): a plain `pip install <path>`
# only bundles the tokenfold* Python package (pyproject.toml's packages.find only
# discovers Python packages) -- this assets/ directory sits OUTSIDE it in the source
# tree, so an installed copy under site-packages has no such sibling at all and
# _hf_counter's Tokenizer.from_file() call fails outright the moment a real request
# needs a non-tiktoken/non-estimate profile (every open-weights model: Qwen, Llama,
# DeepSeek, Mistral, Gemma). TOKENFOLD_ASSETS_DIR lets a deployment point this at the
# real assets directory it already has on disk (e.g. an uninstalled source checkout)
# without needing to restructure the package or duplicate the tokenizer JSON files into
# every venv. Falls through to the original source-tree-relative guess when unset, so
# running directly from an uninstalled checkout (`python -m tokenfold.cli`) is
# unaffected. The real fix is packaging assets/ as package data (MANIFEST.in +
# package-data in pyproject.toml); this env var is the safe stopgap until that lands.
_env_assets = os.environ.get("TOKENFOLD_ASSETS_DIR")
ASSETS = Path(_env_assets) if _env_assets else \
    Path(__file__).resolve().parent.parent.parent / "assets" / "tokenizers"


@dataclass(frozen=True)
class TokenizerProfile:
    name: str                      # short profile id, e.g. "o200k", "qwen2.5"
    kind: str                      # "tiktoken" | "hf" | "estimate"
    counter: Callable[[str], int] = field(compare=False)
    exact: bool = True             # False for estimators

    def count(self, text: str) -> int:
        return self.counter(text)


# ---------------------------------------------------------------------------
# Concrete counters (lazily built, cached)
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=None)
def _tiktoken_counter(encoding: str) -> Callable[[str], int]:
    import tiktoken

    enc = tiktoken.get_encoding(encoding)
    return lambda t: len(enc.encode(t, disallowed_special=()))


@functools.lru_cache(maxsize=None)
def _hf_counter(asset: str) -> Callable[[str], int]:
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(ASSETS / f"{asset}.json"))
    return lambda t: len(tok.encode(t, add_special_tokens=False).ids)


# Claude estimator, calibrated against Anthropic's published guidance
# (~3.5 chars/token English) and o200k as a structural proxy. Marked inexact;
# the AnthropicAdapter swaps in count_tokens API results when configured.
_CJK = re.compile(r"[一-鿿぀-ヿ가-힯]")


def _claude_estimate(text: str) -> int:
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    rest = len(text) - cjk
    # CJK ~1.1 tok/char on Claude-family vocabularies; other text ~1 tok / 3.4 chars
    return max(1, round(cjk * 1.1 + rest / 3.4))


# ---------------------------------------------------------------------------
# Profile table: first regex match on the model id wins
# ---------------------------------------------------------------------------

def _profiles() -> list[tuple[re.Pattern, str, str, Callable[[], Callable[[str], int]], bool]]:
    return [
        # (pattern, profile-name, kind, counter-factory, exact)
        (re.compile(r"gpt-4o|gpt-5|o[134]|chatgpt", re.I), "o200k", "tiktoken",
         lambda: _tiktoken_counter("o200k_base"), True),
        (re.compile(r"gpt-4|gpt-3\.5|text-embedding", re.I), "cl100k", "tiktoken",
         lambda: _tiktoken_counter("cl100k_base"), True),
        (re.compile(r"claude|anthropic", re.I), "claude-est", "estimate",
         lambda: _claude_estimate, False),
        (re.compile(r"qwen", re.I), "qwen2.5", "hf",
         lambda: _hf_counter("qwen2.5"), True),
        (re.compile(r"llama", re.I), "llama3", "hf",
         lambda: _hf_counter("llama3"), True),
        (re.compile(r"deepseek", re.I), "deepseek3", "hf",
         lambda: _hf_counter("deepseek3"), True),
        (re.compile(r"mistral|mixtral|ministral", re.I), "mistral", "hf",
         lambda: _hf_counter("mistral"), True),
        (re.compile(r"gemma", re.I), "gemma2", "hf",
         lambda: _hf_counter("gemma2"), True),
    ]


_FALLBACK = ("o200k", "tiktoken", lambda: _tiktoken_counter("o200k_base"), True)


@functools.lru_cache(maxsize=256)
def profile_for(model_id: str) -> TokenizerProfile:
    """Resolve a model id (e.g. 'gpt-4o-mini', 'qwen2.5:0.5b',
    'hf.co/…Qwen3.5…:Q4_K_M', 'claude-sonnet-5') to a tokenizer profile."""
    for pat, name, kind, factory, exact in _profiles():
        if pat.search(model_id or ""):
            return TokenizerProfile(name=name, kind=kind, counter=factory(), exact=exact)
    name, kind, factory, exact = _FALLBACK
    return TokenizerProfile(name=name, kind=kind, counter=factory(), exact=exact)


def count(text: str, model_id: str) -> int:
    return profile_for(model_id).count(text)


def all_profiles() -> dict[str, TokenizerProfile]:
    """Every distinct profile, for benchmarking."""
    out: dict[str, TokenizerProfile] = {}
    for _, name, kind, factory, exact in _profiles():
        if name not in out:
            try:
                out[name] = TokenizerProfile(name=name, kind=kind, counter=factory(), exact=exact)
            except Exception:
                pass  # missing asset -> skip profile rather than fail
    return out
