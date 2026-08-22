"""TokenFold configuration. Loaded from <config_dir>/config.json, overridable
per-request via headers (adapters map X-TokenFold-* headers onto fields)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from ..paths import config_dir


@dataclass
class Config:
    mode: str = "MAX"                  # FAST | BALANCED | MAX | OFF
    route_mode: str = "human"          # human | agent  (response handling)
    min_confidence: float = 0.99       # semantic confidence floor
    min_savings_pct: float = 8.0       # don't bother below this
    min_savings_abs: int = 6           # ...or below this many tokens
    min_encode_tokens: int = 24        # below this min_savings_abs is unreachable: pass through
    inject_bootstrap: bool = True      # dictionary bootstrap into system prompt
    inject_terse_style: bool = True    # "reply tersely, no preamble" instruction
    fold_history: bool = True
    fold_after_turns: int = 6          # turns kept verbatim (MAX: 1)
    history_style: str = "digest"      # digest | folds
    abstractive_threshold_tokens: int = 8000   # history size to enable tiny-model folds
    tiny_model: str = "qwen2.5:0.5b"   # Ollama tag for background folding
    ollama_url: str = "http://localhost:11434"
    seed_dictionary: bool = True     # install standard alias library
    cache_discount: float = 0.9      # prefix-cache discount on repeat injections
    scope: str = "global"              # dictionary scope (project name etc.)
    log_level: str = "info"            # never logs protected region contents
    upstream: str = "http://localhost:11434/v1"   # default upstream (Ollama)

    def clamp(self) -> "Config":
        self.mode = self.mode.upper()
        if self.mode not in ("FAST", "BALANCED", "MAX", "OFF"):
            self.mode = "BALANCED"
        if self.route_mode not in ("human", "agent"):
            self.route_mode = "human"
        return self


def load() -> Config:
    p = config_dir() / "config.json"
    if p.exists():
        try:
            return Config(**{**asdict(Config()),
                             **json.loads(p.read_text(encoding="utf-8"))}).clamp()
        except Exception:
            pass
    return Config()


def save(cfg: Config) -> None:
    p = config_dir() / "config.json"
    p.write_text(json.dumps(asdict(cfg), indent=1), encoding="utf-8")
