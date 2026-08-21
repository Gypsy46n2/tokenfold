"""Deterministic phrase compression: verbose English -> terse English.

Three rule classes, all pure text transforms (no model):

  1. DELETE  - politeness/filler that carries no semantics
  2. REWRITE - verbose constructions -> terse equivalents (semantics-preserving
               by construction; each rule is individually auditable)
  3. TIGHTEN - whitespace/punctuation normalization

Rules are applied to the *skeleton* (protected regions already lifted), so
they can never touch code, paths, numbers-with-units, quotes, etc.

Every rule preserves negation words; the verifier independently re-checks.
"""

from __future__ import annotations

import re

# --- 1. pure filler: deletable without meaning change --------------------
_DELETE = [
    r"\bplease\b,?\s*",
    r"\bkindly\s+",
    r"\bjust\s+(?=go|make|do|run|check|take|try|add|use)",
    r"\bgo ahead and\s+",
    r"\bfeel free to\s+",
    r"\bi(?:'d| would) like you to\s+",
    r"\bi want you to\s+",
    r"\bi need you to\s+",
    r"\bcould you(?: please)?\s+",
    r"\bcan you(?: please)?\s+",
    r"\bwould you(?: please)?\s+",
    r"\bi was wondering if you could\s+",
    r"\bhey,?\s+",
    r"\bas always,?\s+",
    r"\bremember(?: that)?,?\s+",
    r"\bnote that\s+",
    r"\bbasically,?\s+",
    r"\bessentially,?\s+",
    r"\bactually,?\s+",
]

# --- 2. verbose -> terse rewrites (LHS regex, RHS replacement) ----------
_REWRITE: list[tuple[str, str]] = [
    (r"\bin order to\b", "to"),
    (r"\bmake sure(?: that)?(?: you)?\b", "ensure"),
    (r"\bidentify any\b", "find"),
    (r"\bidentify\b", "find"),
    (r"\byou can find\b", ""),
    (r"\ball of them\b", "all"),
    (r"\beach of the\b", "each"),
    (r"\bthe following\b", "this"),
    (r"\bas follows\b", ":"),
    (r"\bwhether or not\b", "whether"),
    (r"\bat this point in time\b", "now"),
    (r"\bin the event that\b", "if"),
    (r"\bdue to the fact that\b", "because"),
    (r"\bwith respect to\b", "re"),
    (r"\bwith regard to\b", "re"),
    (r"\bin terms of\b", "re"),
    (r"\ba complete list of\b", "all"),
    (r"\bthe complete list of\b", "all"),
    (r"\bevery place where\b", "everywhere"),
    (r"\btogether with\b", "with"),
    (r"\balong with\b", "with"),
    (r"\bas well as\b", "and"),
    (r"\bis able to\b", "can"),
    (r"\bare able to\b", "can"),
    (r"\bit is necessary to\b", "must"),
    (r"\byou should\b", ""),
    (r"\byour task is to\b", ""),
    (r"\bwhat i want is\b", ""),
    (r"\bthe existing functionality\b", "existing behavior"),
    (r"\bfunctionality\b", "behavior"),
    (r"\bdocumentation\b", "docs"),
    (r"\brepository\b", "repo"),
    (r"\bapplication\b", "app"),
    (r"\bconfiguration\b", "config"),
    (r"\bimplementation\b", "impl"),
    (r"\benvironment\b", "env"),
    (r"\bdirectory\b", "dir"),
    (r"\bapproximately\b", "~"),
    (r"\bgreater than or equal to\b", ">="),
    (r"\bless than or equal to\b", "<="),
    (r"\bat least (\d+)\b", r">=\1"),
    (r"\bat most (\d+)\b", r"<=\1"),
    # NB: never rewrite bare "greater/less than N" -> symbols around negation;
    # verifier checks comparators either way.
]

# words that must never be deleted or merged away (safety net; the verifier
# also checks these). Rules above are constructed not to touch them.
NEGATIONS = re.compile(
    r"\b(?:not|no|never|don'?t|do not|won'?t|cannot|can'?t|shouldn'?t|"
    r"mustn'?t|without|except|unless|only)\b", re.I)

_DELETE_RE = [re.compile(p, re.I) for p in _DELETE]
_REWRITE_RE = [(re.compile(p, re.I), r) for p, r in _REWRITE]
_WS = re.compile(r"[ \t]{2,}")
_SPACE_PUNCT = re.compile(r"\s+([,;:.!?])")
_MULTI_PUNCT = re.compile(r"([,;:])\s*\1+")


def compress(skeleton: str) -> str:
    """Apply deterministic phrase compression to a protected skeleton."""
    out = skeleton
    for pat in _DELETE_RE:
        out = pat.sub("", out)
    for pat, rep in _REWRITE_RE:
        out = pat.sub(rep, out)
    # tighten
    out = _SPACE_PUNCT.sub(r"\1", out)
    out = _MULTI_PUNCT.sub(r"\1", out)
    out = _WS.sub(" ", out)
    out = re.sub(r"^[ ,;]+|[ \t]+$", "", out, flags=re.M)
    # sentence-initial capitalization is meaningless post-compression; leave as is
    return out


def negation_signature(text: str) -> list[str]:
    """Ordered list of negation tokens; must be identical pre/post compression."""
    return [m.group(0).lower().replace("'", "") for m in NEGATIONS.finditer(text)]
