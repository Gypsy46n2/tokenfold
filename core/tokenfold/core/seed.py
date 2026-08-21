"""Seed dictionary: a standard library of agent-instruction aliases.

These cover the boilerplate that agent frameworks retransmit constantly.
Surface forms are deliberately flexible regexes: they match common phrasings
both before and after terse-English rewriting (e.g. functionality/behavior).

Seeding happens once (generation 1) when a dictionary has no generations and
config.seed_dictionary is true.
"""

from __future__ import annotations

from .dictionary import Alias, Dictionary, Generation

# (code, canonical expansion, surface-form regexes)
SEEDS: list[tuple[str, str, list[str]]] = [
    ("K1", "analyze the program",
     [r"analy[sz]e th(?:is|e) (?:following )?(?:program|code)"]),
    ("K2", "preserve existing behavior",
     [r"(?:ensure )?(?:you )?preserve (?:the )?existing (?:functionality|behavior)",
      r"keep (?:the )?existing (?:functionality|behavior)(?: intact)?",
      r"maintain (?:the )?existing (?:functionality|behavior)"]),
    ("K3", "do not break existing behavior",
     [r"do(?: |n['o]?t )not break (?:any )?existing (?:functionality|behavior)",
      r"do not break (?:any )?existing (?:functionality|behavior)",
      r"don'?t break (?:any )?existing (?:functionality|behavior)",
      r"without breaking (?:any )?existing (?:functionality|behavior)"]),
    ("K4", "run the full test suite after changes",
     [r"run (?:the )?(?:full )?test(?:s| suite)(?: afterwards| after(?:wards)?| before finishing)?"]),
    ("K5", "preserve all public APIs exactly",
     [r"preserve all public APIs?(?: exactly(?: as they are)?)?",
      r"keep (?:all )?public APIs?(?: stable| unchanged)?"]),
    ("K6", "update documentation to match changes",
     [r"update (?:the )?docs?(?:umentation)?(?: (?:to match|whenever behavior changes|accordingly))?"]),
    ("K7", "do not modify unrelated files",
     [r"(?:do not|don'?t|never) (?:modify|touch|change) (?:any )?(?:files? )?(?:that are )?unrelated(?: to (?:this|the current) task)?(?: files?)?",
      r"(?:do not|don'?t|never) modify (?:any )?unrelated (?:files?|code)"]),
    ("K8", "return the complete implementation",
     [r"return (?:the |a )?complete impl(?:ementation)?",
      r"give (?:me )?(?:the |a )?(?:full|complete) impl(?:ementation)?"]),
    ("K9", "explain each change made",
     [r"explain (?:each|all)(?: of)?(?: the)? changes(?: (?:you |that were )?made)?",
      r"explain (?:each|every) change(?: you made)?"]),
    ("K10", "search project files",
     [r"search (?:the )?project(?: files)? for",
      r"search (?:all )?files in (?:the )?(?:project|repo) for"]),
    ("K11", "list file paths with line numbers",
     [r"(?:return |list |give )?(?:all )?(?:the )?file paths (?:together )?with(?: the)? line numbers"]),
    ("K12", "fix all bugs found",
     [r"(?:find and )?fix (?:all|any)(?: of)?(?: the)? bugs(?: (?:you |that you )?(?:can )?f(?:i|ou)nd)?"]),
    ("K13", "add unit tests covering the change",
     [r"add (?:unit )?tests? (?:covering|for) (?:the|this|that) (?:change|fix|new behavior)"]),
    ("K14", "think step by step before answering",
     [r"think (?:carefully )?step[- ]by[- ]step(?: before (?:you )?answer(?:ing)?)?"]),
    ("K15", "cite sources for factual claims",
     [r"(?:cite|include|provide) (?:your )?sources(?: for (?:all )?factual claims)?"]),
]

# Policy bundles: an ordered K-sequence that collapses to one code
BUNDLES: list[tuple[str, list[str], str]] = [
    ("P1", ["K3", "K4", "K5", "K6", "K7"], "standing project rules"),
]


def seed(d: Dictionary) -> Generation | None:
    """Install the seed aliases as generation 1 if the dictionary is empty."""
    if d.generations:
        return None
    for code, expansion, forms in SEEDS:
        d.aliases[code] = Alias(code=code, expansion=expansion, surface_forms=forms)
    for pcode, members, label in BUNDLES:
        exp = f"{label}: " + " + ".join(
            d.aliases[m].expansion for m in members if m in d.aliases)
        d.aliases[pcode] = Alias(code=pcode, expansion=exp, surface_forms=[],
                                 members=list(members))
    codes = sorted(d.aliases.keys(), key=lambda c: (c[0], int(c[1:])))
    gen = Generation(version=1, codes=codes,
                     bootstrap=d._render_bootstrap(1, codes))
    d.generations.append(gen)
    d.save()
    return gen


def bundle_patterns() -> list[tuple[str, str]]:
    """(regex, replacement) collapsing K-sequences into P codes.
    Members may appear separated by ; , . 'and' or spaces, in seed order."""
    out = []
    for pcode, members, _ in BUNDLES:
        sep = r"[;,.]?\s*(?:and\s+)?"
        pat = sep.join(members)
        out.append((pat, pcode))
    return out
