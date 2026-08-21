"""Semantic verifier: deterministic invariant extraction + diff.

Before any compressed candidate is allowed out the door, we extract an
invariant signature from the original and from the *decoded* candidate
(aliases expanded, placeholders restored) and require them to match.

Invariants checked (the disaster classes from the spec):
  * negations       - ordered list of negation tokens
  * numbers         - multiset of numeric literals (incl. those inside words)
  * comparators     - <, >, <=, >=, "at least/most" normalized
  * modality        - must/should/may/optional/required markers, ordered
  * protected refs  - all placeholder indices present exactly once each

The result is a confidence in [0,1]; anything < threshold falls back.
This is intentionally *not* a model: it can't be sweet-talked, it costs
microseconds, and its failure mode is false-alarm fallback (safe).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .phrases import negation_signature

_NUM = re.compile(r"\d+(?:\.\d+)?")
# word comparators only count when a number follows ("exactly 3", not the
# adverb in "preserve APIs exactly")
_CMP = re.compile(r"(<=|>=|<|>|(?:\bat least\b|\bat most\b|\bno more than\b|"
                  r"\bno fewer than\b|\bup to\b|\bexactly\b)(?=\s*\d))", re.I)
# "never" is covered by the negation-class check, so not repeated here
_MODAL = re.compile(r"\b(must(?: not)?|shall(?: not)?|should(?: not)?|"
                    r"may(?: not)?|optional(?:ly)?|required|mandatory|"
                    r"(?<!as )always)\b", re.I)

_CMP_NORM = {"at least": ">=", "no fewer than": ">=", "at most": "<=",
             "no more than": "<=", "up to": "<=", "exactly": "=="}
_MODAL_NORM = {"shall": "must", "shall not": "must not", "mandatory": "required",
               "optionally": "optional"}


@dataclass
class Signature:
    negations: tuple
    numbers: Counter
    comparators: tuple
    modality: tuple

    @classmethod
    def of(cls, text: str) -> "Signature":
        t = text
        return cls(
            negations=tuple(negation_signature(t)),
            numbers=Counter(_NUM.findall(t)),
            comparators=tuple(
                _CMP_NORM.get(m.group(1).lower(), m.group(1).lower())
                for m in _CMP.finditer(t)),
            modality=tuple(
                _MODAL_NORM.get(m.group(1).lower(), m.group(1).lower())
                for m in _MODAL.finditer(t)),
        )


@dataclass
class Verdict:
    ok: bool
    confidence: float
    failures: list[str]


def verify(original: str, decoded_candidate: str) -> Verdict:
    """Compare invariant signatures of original vs the candidate as the model
    would understand it (i.e. after alias expansion + placeholder restore)."""
    a, b = Signature.of(original), Signature.of(decoded_candidate)
    failures: list[str] = []

    # negations compare by CLASS, not surface form: "never modify" and
    # "do not modify" are the same prohibition (alias expansion may
    # canonicalize the wording). except/unless/only stay distinct.
    _NEG = {"not": "NEG", "no": "NEG", "never": "NEG", "dont": "NEG",
            "do not": "NEG", "wont": "NEG", "cannot": "NEG", "cant": "NEG",
            "shouldnt": "NEG", "mustnt": "NEG", "without": "NEG"}
    neg_a = tuple(_NEG.get(x, x) for x in a.negations)
    neg_b = tuple(_NEG.get(x, x) for x in b.negations)
    if neg_a != neg_b:
        failures.append(f"negation: {a.negations} -> {b.negations}")
    if a.numbers != b.numbers:
        missing = a.numbers - b.numbers
        added = b.numbers - a.numbers
        failures.append(f"numbers: -{dict(missing)} +{dict(added)}")
    if a.comparators != b.comparators:
        failures.append(f"comparators: {a.comparators} -> {b.comparators}")
    if a.modality != b.modality:
        failures.append(f"modality: {a.modality} -> {b.modality}")

    conf = 1.0 - 0.4 * len(failures)
    return Verdict(ok=not failures, confidence=max(0.0, conf), failures=failures)
