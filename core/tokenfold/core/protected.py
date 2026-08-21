"""Protected-region extraction.

Anything that must survive byte-for-byte (code, paths, URLs, keys, JSON,
numbers-in-context, quotes, ...) is lifted out before any compression and
restored afterwards. Placeholders are chosen to be tokenizer-cheap and
collision-safe: ⟦n⟧ (mathematical white square brackets around an index) never
occurs in normal prompts; a pre-scan guarantees collision safety anyway.

extract(text)  -> (skeleton, regions)   # skeleton contains ⟦0⟧, ⟦1⟧ ...
restore(skeleton, regions) -> text      # exact inverse, verified
"""

from __future__ import annotations

import re
from dataclasses import dataclass

PH_OPEN, PH_CLOSE = "⟦", "⟧"   # ⟦ ⟧
_PH_RE = re.compile(re.escape(PH_OPEN) + r"(\d+)" + re.escape(PH_CLOSE))

# Order matters: larger/structural regions first so smaller patterns never
# split them. Each entry: (kind, compiled regex).
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("code_fence", re.compile(r"```.*?```", re.S)),
    ("json_block", re.compile(r"(?<![\w⟦])\{(?:[^{}]|\{[^{}]*\})*\}(?!\w)", re.S)),
    ("xml_block", re.compile(r"<([A-Za-z][\w.-]*)(?:\s[^<>]*)?>.*?</\1>", re.S)),
    ("inline_code", re.compile(r"`[^`\n]+`")),
    ("url", re.compile(r"\b(?:https?|ftp|file)://[^\s<>\"')\]]+")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("win_path", re.compile(r"\b[A-Za-z]:[\\/](?:[^\s:*?\"<>|]+[\\/]?)+")),
    ("unc_path", re.compile(r"\\\\[\w.-]+\\[^\s\"<>|]+")),
    ("posix_path", re.compile(r"(?<![\w.])(?:~?/[\w.-]+){2,}/?")),
    ("api_key", re.compile(
        r"\b(?:sk|pk|rk|ghp|gho|xox[bap]|AKIA|ASIA)[-_A-Za-z0-9]{12,}\b")),
    ("bearer", re.compile(r"\b(?:Bearer|Token)\s+[A-Za-z0-9._~+/=-]{16,}", re.I)),
    ("uuid", re.compile(
        r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
        r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")),
    ("hash", re.compile(r"\b[0-9a-fA-F]{32,128}\b")),
    ("base64", re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")),
    ("env_assign", re.compile(r"\b[A-Z][A-Z0-9_]{2,}=[^\s;,]+")),
    ("quoted", re.compile(r"\"[^\"\n]{2,}\"|'[^'\n]{2,}'")),
    ("regex_lit", re.compile(r"(?<![\w])/(?:[^/\\\n]|\\.)+/[gimsuxy]*(?![\w])")),
    ("semver", re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b")),
    ("number_unit", re.compile(
        r"\b\d[\d,]*(?:\.\d+)?\s?(?:%|ms|s|m|h|d|px|em|kb|mb|gb|tb|kib|mib|gib|"
        r"hz|khz|mhz|ghz|usd|eur|gbp|\$|€|tokens?|bytes?)\b", re.I)),
    ("date", re.compile(
        r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?\b")),
    ("identifier", re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")),   # snake_case
    ("camel_ident", re.compile(r"\b[a-z]+(?:[A-Z][a-z0-9]*){1,}\b")),   # camelCase
    ("dotted_call", re.compile(r"\b[\w]+(?:\.[\w]+)+\(?\)?")),          # a.b.c()
    ("filename", re.compile(
        r"\b[\w-]+\.(?:py|js|ts|tsx|jsx|json|yaml|yml|toml|md|txt|csv|xml|html|"
        r"css|sql|sh|ps1|bat|exe|dll|so|rs|go|java|c|cpp|h|hpp|ipynb|lock|env|cfg|ini)\b", re.I)),
]


@dataclass
class Region:
    kind: str
    text: str


def extract(text: str) -> tuple[str, list[Region]]:
    """Lift protected regions out of *text*, returning skeleton + regions."""
    # collision safety: escape any pre-existing placeholder-shaped text
    if PH_OPEN in text:
        text = text.replace(PH_OPEN, PH_OPEN + "​")
    regions: list[Region] = []
    skeleton = text
    for kind, pat in _PATTERNS:
        def _sub(m: re.Match, kind=kind) -> str:
            # never re-capture placeholders
            if _PH_RE.fullmatch(m.group(0)):
                return m.group(0)
            regions.append(Region(kind, m.group(0)))
            return f"{PH_OPEN}{len(regions) - 1}{PH_CLOSE}"
        skeleton = pat.sub(_sub, skeleton)
    return skeleton, regions


def restore(skeleton: str, regions: list[Region]) -> str:
    """Exact inverse of extract(). Raises if a placeholder is missing."""
    seen: set[int] = set()

    def _sub(m: re.Match) -> str:
        i = int(m.group(1))
        seen.add(i)
        return regions[i].text

    out = _PH_RE.sub(_sub, skeleton)
    # regions may nest (a region captured an earlier placeholder): keep
    # expanding until stable, bounded by region count
    for _ in range(len(regions)):
        if not _PH_RE.search(out):
            break
        out = _PH_RE.sub(_sub, out)
    missing = set(range(len(regions))) - seen
    if missing:
        raise ValueError(f"placeholders lost during compression: {sorted(missing)}")
    out = out.replace(PH_OPEN + "​", PH_OPEN)
    return out


def verify_roundtrip(text: str) -> bool:
    skel, regs = extract(text)
    return restore(skel, regs) == text
