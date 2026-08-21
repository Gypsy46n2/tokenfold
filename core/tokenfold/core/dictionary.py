"""Generational alias dictionary.

Codes:
  K<n>  instruction/concept aliases   ("preserve existing behavior")
  P<n>  policy bundles                (sets of K codes sent as standing rules)
  E<n>  session entities              ("Agent Manager") - session scoped

Design (locked in grilling Q8):
  * A *generation* is a frozen, byte-stable bootstrap block -> prefix-cache
    friendly. Newly learned aliases accumulate as candidates ("nursery") and
    are only usable once a new generation is minted.
  * Minting rule:  net = (saved_per_use * expected_uses) - definition_cost
                        - cache_miss_penalty  must be > 0.
  * Scopes: global / per-project / per-model, layered in that order; later
    layers may add but never redefine earlier codes.

Persistence: one JSON file per scope under paths.dictionaries_dir().
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from ..paths import dictionaries_dir

_CODE_RE = re.compile(r"\b([KPE]\d+)\b")


@dataclass
class Alias:
    code: str                 # "K17"
    expansion: str            # canonical English expansion
    surface_forms: list[str] = field(default_factory=list)  # regexes that match it
    uses: int = 0
    created: float = field(default_factory=time.time)
    members: list[str] | None = None   # P-codes: member K-codes


@dataclass
class Generation:
    version: int
    codes: list[str]                     # codes frozen into this generation
    bootstrap: str                       # the exact byte-stable block
    minted: float = field(default_factory=time.time)


class Dictionary:
    """One scope's dictionary (global, project, or model)."""

    def __init__(self, scope: str = "global", path: Optional[Path] = None):
        self.scope = scope
        self.path = path or (dictionaries_dir() / f"{scope}.json")
        self.aliases: dict[str, Alias] = {}
        self.generations: list[Generation] = []
        self.nursery: dict[str, dict] = {}   # phrase -> {count, tokens, first_seen}
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        if self.path.exists():
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                self.aliases = {c: Alias(**a) for c, a in raw.get("aliases", {}).items()}
                self.generations = [Generation(**g) for g in raw.get("generations", [])]
                self.nursery = raw.get("nursery", {})
            except Exception:
                # corrupt dictionary must never block traffic
                self.aliases, self.generations, self.nursery = {}, [], {}

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({
            "aliases": {c: asdict(a) for c, a in self.aliases.items()},
            "generations": [asdict(g) for g in self.generations],
            "nursery": self.nursery,
        }, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    # -- querying --------------------------------------------------------
    @property
    def active_generation(self) -> Optional[Generation]:
        return self.generations[-1] if self.generations else None

    def bootstrap_block(self) -> str:
        """The exact text to inject (byte-stable per generation)."""
        gen = self.active_generation
        return gen.bootstrap if gen else ""

    def established_codes(self) -> set[str]:
        gen = self.active_generation
        return set(gen.codes) if gen else set()

    def substitutable(self) -> list[tuple[re.Pattern, str]]:
        """(pattern, code) pairs for codes usable right now (frozen in the
        active generation), longest surface form first."""
        out: list[tuple[re.Pattern, str, int]] = []
        for code in self.established_codes():
            a = self.aliases.get(code)
            if not a:
                continue
            for sf in a.surface_forms:
                out.append((re.compile(sf, re.I), code, len(sf)))
        out.sort(key=lambda t: -t[2])
        return [(p, c) for p, c, _ in out]

    def expansions(self) -> dict[str, str]:
        return {c: a.expansion for c, a in self.aliases.items()}

    # -- learning --------------------------------------------------------
    def observe(self, phrase: str, token_cost: int) -> None:
        """Record a recurring phrase sighting (from the learner)."""
        key = phrase.strip().lower()
        if len(key) < 12:      # too short to ever pay off
            return
        n = self.nursery.setdefault(key, {"count": 0, "tokens": token_cost,
                                          "first_seen": time.time()})
        n["count"] += 1
        n["tokens"] = token_cost

    def promotable(self, min_net: int = 20, code_cost: int = 2,
                   def_overhead: int = 6, horizon_uses: int = 20,
                   cache_miss_penalty: int = 40) -> list[tuple[str, int]]:
        """Nursery phrases whose expected net savings clear the bar.

        net = (tokens - code_cost) * expected_uses - (tokens + def_overhead)
        expected_uses = min(observed_count * 2, horizon_uses)
        The cache-miss penalty is charged once per *minting event*, so it is
        subtracted from the total of the batch by mint(), not per phrase.
        """
        out = []
        for phrase, n in self.nursery.items():
            exp_uses = min(n["count"] * 2, horizon_uses)
            net = (n["tokens"] - code_cost) * exp_uses - (n["tokens"] + def_overhead)
            if net >= min_net and n["count"] >= 2:
                out.append((phrase, net))
        out.sort(key=lambda t: -t[1])
        return out

    def mint(self, cache_miss_penalty: int = 40, **kw) -> Optional[Generation]:
        """Freeze promotable nursery phrases into a new generation, if the
        batch clears the cache-miss penalty. Returns the new generation."""
        cands = self.promotable(**kw)
        total_net = sum(net for _, net in cands)
        if not cands or total_net <= cache_miss_penalty:
            return None
        next_idx = 1 + max(
            (int(c[1:]) for c in self.aliases if c.startswith("K")), default=0)
        for phrase, _ in cands:
            code = f"K{next_idx}"
            next_idx += 1
            self.aliases[code] = Alias(
                code=code, expansion=phrase,
                surface_forms=[re.escape(phrase)])
            self.nursery.pop(phrase, None)
        version = (self.active_generation.version + 1) if self.generations else 1
        codes = sorted(self.aliases.keys(),
                       key=lambda c: (c[0], int(c[1:])))
        bootstrap = self._render_bootstrap(version, codes)
        gen = Generation(version=version, codes=codes, bootstrap=bootstrap)
        self.generations.append(gen)
        self.save()
        return gen

    def add_manual(self, code: str, expansion: str,
                   surface_forms: Optional[list[str]] = None) -> None:
        self.aliases[code] = Alias(code=code, expansion=expansion,
                                   surface_forms=surface_forms or [re.escape(expansion)])

    def remove(self, code: str) -> None:
        self.aliases.pop(code, None)

    def _render_bootstrap(self, version: int, codes: list[str]) -> str:
        parts = []
        for c in codes:
            a = self.aliases[c]
            if a.members:
                parts.append(f"{c}={'+'.join(a.members)}")   # bundle by reference
            else:
                parts.append(f"{c}={a.expansion}")
        return (f"DICT v{version} (shorthand; expand mentally): "
                + "; ".join(parts))

    def bootstrap_for(self, codes: list[str]) -> str:
        """Subset bootstrap in the given (grow-only) order; byte-stable as
        long as the code list only appends."""
        gen = self.active_generation
        if not gen or not codes:
            return ""
        known = [c for c in codes if c in self.aliases]
        return self._render_bootstrap(gen.version, known) if known else ""

    # -- import/export ---------------------------------------------------
    def export_json(self) -> str:
        return json.dumps({
            "scope": self.scope,
            "aliases": {c: asdict(a) for c, a in self.aliases.items()},
            "generations": [asdict(g) for g in self.generations],
        }, indent=1, ensure_ascii=False)

    def import_json(self, blob: str) -> None:
        raw = json.loads(blob)
        for c, a in raw.get("aliases", {}).items():
            self.aliases.setdefault(c, Alias(**a))
        self.save()


def find_codes(text: str) -> list[str]:
    """All K/P/E codes referenced in a text."""
    return _CODE_RE.findall(text)
