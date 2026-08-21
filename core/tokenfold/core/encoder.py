"""Outbound encoder: OpenAI-style message list in, cheaper message list out.

Pipeline per request (grilling-locked order of weapons):
  1. session resolve + entity observation
  2. history folding (old turns -> extractive/abstractive folds; blob dedup)
  3. per-message candidates for the *new* content:
        A  original
        B  phrase-compressed (terse English)         [FAST+]
        C  B + alias/entity substitution             [FAST+ if established]
     each candidate is verified (invariant diff on its decoded form) and
     token-counted with the destination model's tokenizer; cheapest passing
     candidate wins if it clears min-savings thresholds.
  4. stable injections: dictionary bootstrap + terse-style instruction
  5. anything fails -> passthrough of the original (never block traffic)

Returns (encoded_messages, EncodeReport).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from . import phrases, protected, verifier
from .config import Config
from .dictionary import Dictionary
from .folding import AbstractiveFolder, dedup_blob, fold_turn
from .session import Session, session_id_for
from ..tokenizers.registry import profile_for

TERSE_STYLE = "Reply terse, no preamble/summary. Code/paths/numbers exact."

_CLAUSE_SPLIT = re.compile(r"[;]|(?<=[.!?])\s+")


@dataclass
class MessageReport:
    index: int
    representation: str            # "original" | "terse" | "terse+alias" | "fold"
    original_tokens: int
    encoded_tokens: int
    confidence: float = 1.0
    failures: list[str] = field(default_factory=list)


@dataclass
class EncodeReport:
    session_id: str
    model: str
    profile: str
    exact_tokenizer: bool
    original_tokens: int = 0
    encoded_tokens: int = 0
    dictionary_overhead: int = 0
    latency_ms: float = 0.0
    fallback: bool = False
    messages: list[MessageReport] = field(default_factory=list)

    @property
    def saved(self) -> int:
        return self.original_tokens - self.encoded_tokens - self.dictionary_overhead

    @property
    def pct(self) -> float:
        return (self.saved / self.original_tokens * 100) if self.original_tokens else 0.0


class Encoder:
    def __init__(self, cfg: Config, dictionary: Dictionary | None = None,
                 folder: AbstractiveFolder | None = None):
        self.cfg = cfg
        self.dict = dictionary or Dictionary(scope=cfg.scope)
        self.folder = folder

    # ------------------------------------------------------------------
    def encode(self, messages: list[dict], model: str,
               session_id: str | None = None) -> tuple[list[dict], EncodeReport]:
        t0 = time.perf_counter()
        prof = profile_for(model)
        report = EncodeReport(session_id="", model=model, profile=prof.name,
                              exact_tokenizer=prof.exact)
        try:
            return self._encode(messages, model, prof, report, t0, session_id)
        except Exception:
            # FAILURE BEHAVIOR: passthrough, always.
            report.fallback = True
            report.encoded_tokens = report.original_tokens = self._count_all(messages, prof)
            report.latency_ms = (time.perf_counter() - t0) * 1000
            return messages, report

    # ------------------------------------------------------------------
    def _count_all(self, messages: list[dict], prof) -> int:
        return sum(prof.count(self._text(m)) for m in messages)

    @staticmethod
    def _text(m: dict) -> str:
        c = m.get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):     # multi-part content: only compress text parts
            return "".join(p.get("text", "") for p in c if isinstance(p, dict))
        return ""

    # ------------------------------------------------------------------
    def _encode(self, messages: list[dict], model: str, prof,
                report: EncodeReport, t0: float,
                session_id: str | None = None) -> tuple[list[dict], EncodeReport]:
        cfg = self.cfg
        sid = session_id or session_id_for(messages)
        report.session_id = sid
        session = Session(sid)
        session.turn += 1

        if cfg.mode == "OFF":
            report.original_tokens = report.encoded_tokens = self._count_all(messages, prof)
            report.latency_ms = (time.perf_counter() - t0) * 1000
            return messages, report

        # observe entities across all user/system text
        for m in messages:
            if m.get("role") in ("user", "system"):
                session.observe_entities(self._text(m))

        out: list[dict] = []
        n = len(messages)
        keep = cfg.fold_after_turns
        if cfg.mode == "MAX":
            keep = 1                     # MAX: only the newest turn verbatim
        fold_cutoff = max(0, n - keep) if cfg.fold_history else 0

        # token budget of full history for abstractive trigger
        hist_tokens = self._count_all(messages, prof)

        digest_mode = cfg.fold_history and cfg.history_style == "digest"

        # Two complete candidate encodings are built in parallel:
        #   A) alias set  (K/P/E codes; needs dictionary/entity injections)
        #   B) noalias set (terse/folds only; needs no injections)
        # After injection overhead is known, the cheaper TOTAL wins.
        out_noalias: list[dict] = []
        old_hashes: set[str] = set()
        for i, m in enumerate(messages):
            text = self._text(m)
            role = m.get("role", "user")
            orig_tok = prof.count(text)
            report.original_tokens += orig_tok

            if not text or not isinstance(m.get("content"), str):
                out.append(m)
                out_noalias.append(m)
                report.encoded_tokens += orig_tok
                continue

            is_old = i < fold_cutoff and role in ("user", "assistant", "tool") and i > 0
            if is_old and role == "tool":
                # tool results: keep the message (API pairing with tool_calls)
                # but replace bulky old content with a reference
                if len(text) > 200:
                    from .session import content_hash as _ch
                    h, _seen = session.note_blob(text, "tool")
                    enc = f"[ref:{h} tool-result; expand on request]"
                    out.append({**m, "content": enc})
                    out_noalias.append({**m, "content": enc})
                    report.encoded_tokens += prof.count(enc)
                    report.messages.append(MessageReport(
                        index=i, representation="fold", original_tokens=orig_tok,
                        encoded_tokens=prof.count(enc)))
                else:
                    out.append(m)
                    out_noalias.append(m)
                    report.encoded_tokens += orig_tok
                continue
            if is_old and digest_mode:
                from .session import content_hash as _ch
                if _ch(text) in session.pending_expansions:
                    # the model asked for this turn's content: ship it once
                    session.pending_expansions.remove(_ch(text))
                    out.append(m)
                    out_noalias.append(m)
                    report.encoded_tokens += orig_tok
                    report.messages.append(MessageReport(
                        index=i, representation="expanded",
                        original_tokens=orig_tok, encoded_tokens=orig_tok))
                    continue
                # rolling digest: absorb once, then omit the turn entirely
                old_hashes.add(self._absorb_into_digest(text, role, session))
                report.messages.append(MessageReport(
                    index=i, representation="digest", original_tokens=orig_tok,
                    encoded_tokens=0))
                continue
            if is_old:
                enc_na, enc, rep = self._fold_old_turn(text, session, i, hist_tokens)
                conf, fails = 1.0, []
            else:
                enc_na, enc, rep, conf, fails = self._compress_new(
                    text, role, prof, session)
                report.messages.append(MessageReport(
                    index=i, representation=rep, original_tokens=orig_tok,
                    encoded_tokens=prof.count(enc), confidence=conf, failures=fails))

            # never ship a longer message than the original
            if prof.count(enc) >= orig_tok:
                enc = text
            if prof.count(enc_na) >= orig_tok:
                enc_na = text
            out.append({**m, "content": enc})
            out_noalias.append({**m, "content": enc_na})

        # ---- rolling digest block (one message for ALL old turns) ------
        # Only lines whose source turn is present in THIS request: a session
        # resumed with different history must not leak stale digest lines.
        window = 15 if cfg.mode == "MAX" else 30
        digest_lines = [e["line"] for e in session.digest_entries
                        if any(h in old_hashes
                               for h in (e.get("hs") or [e.get("h")]))][-window:]
        if self.folder and digest_mode:
            # background: squeeze old digest lines with the tiny model
            self.folder.maybe_compact_digest(session, window)
        if digest_mode and digest_lines:
            base = "HIST (compact digest of earlier turns; ask to expand any "\
                   "[code:...] ref): " + " | ".join(digest_lines)
            aliased = self._alias_pass(base, session)
            pos = 1 if out and out[0].get("role") == "system" else 0
            out.insert(pos, {"role": "system", "content": aliased})
            out_noalias.insert(pos, {"role": "system", "content": base})

        # ---- stable injections (system head) --------------------------
        # The upstream API is stateless: definitions must ride along on EVERY
        # request whose encoded content uses codes. Blocks are byte-stable
        # (per dictionary generation / per session entity set), so provider
        # prefix caching amortizes their real cost.
        inject_parts: list[str] = []
        if cfg.inject_bootstrap:
            from .dictionary import find_codes
            est = self.dict.established_codes()
            used = [c for m in out for c in find_codes(self._text(m)) if c in est]
            # P-bundles are defined by member reference: pull members in too
            for code in list(used):
                al = self.dict.aliases.get(code)
                if al and al.members:
                    used += [mm for mm in al.members if mm not in used]
            # grow-only session order keeps the block prefix-stable
            for c in used:
                if c not in session.used_codes:
                    session.used_codes.append(c)
            if used:
                bs = self.dict.bootstrap_for(session.used_codes)
                if bs:
                    inject_parts.append(bs)
        ent_block = session.entity_block()
        if ent_block and self._entity_codes_used(out, session):
            inject_parts.append(ent_block)
        block = "\n".join(inject_parts) if inject_parts else ""
        overhead = prof.count(block) if block else 0
        # cache-aware effective overhead: a byte-identical injection block was
        # already in this session's previous request prefix -> provider prefix
        # caching makes its real cost a fraction of face value.
        from .session import content_hash
        block_hash = content_hash(block) if block else ""
        eff_overhead = overhead
        if block and (block_hash == session.last_inject_hash
                      or session.turn >= 2):
            # already-cached block, or a proven multi-turn session where the
            # one-time send amortizes over future turns: judge at steady-state
            # (discounted) cost. One-shot requests stay at face value.
            eff_overhead = round(overhead * (1 - cfg.cache_discount))

        # total-cost decision: alias set + injections vs terse-only set.
        # (The terse-style instruction is exempt: it pays for itself in
        # output tokens.)
        total_alias = sum(prof.count(self._text(m)) for m in out)
        total_noalias = sum(prof.count(self._text(m)) for m in out_noalias)
        if total_noalias <= total_alias + eff_overhead:
            out = out_noalias
            inject_parts = []
            report.encoded_tokens = total_noalias
            report.dictionary_overhead = 0
        else:
            report.encoded_tokens = total_alias
            report.dictionary_overhead = overhead
            session.last_inject_hash = block_hash

        # ---- absolute invariant: NEVER ship more than the original ------
        if report.encoded_tokens + report.dictionary_overhead \
                >= report.original_tokens and report.original_tokens:
            if report.encoded_tokens + report.dictionary_overhead \
                    > report.original_tokens:
                report.fallback = True
            report.encoded_tokens = report.original_tokens
            report.dictionary_overhead = 0
            out = list(messages)
            inject_parts = []

        # style injection: output-side savings, always allowed in human mode
        if cfg.inject_terse_style and cfg.route_mode == "human":
            style = TERSE_STYLE
            if inject_parts:   # a DICT/ENT block rides along: codes are legal
                style += " You may use DICT/ENT codes in replies."
            inject_parts.append(style)

        if inject_parts:
            block = "\n".join(inject_parts)
            if out and out[0].get("role") == "system" and isinstance(out[0].get("content"), str):
                out[0] = {**out[0], "content": out[0]["content"] + "\n\n" + block}
            else:
                out.insert(0, {"role": "system", "content": block})

        session.save()
        report.latency_ms = (time.perf_counter() - t0) * 1000
        return out, report

    # ------------------------------------------------------------------
    def _alias_pass(self, text: str, session: Session) -> str:
        """Exact-meaning alias/entity/bundle substitution (no verification
        needed: substitutions are definitionally meaning-preserving)."""
        out = text
        for pat, code in self.dict.substitutable():
            out = pat.sub(code, out)
        from .seed import bundle_patterns
        import re as _re
        for bp, pcode in bundle_patterns():
            out = _re.sub(bp, pcode, out)
        for name, code in session.entity_pairs():
            out = out.replace(name, code)
        return out

    def _absorb_into_digest(self, text: str, role: str, session: Session) -> str:
        """Fold a turn's essence into the rolling digest, once per content.
        Returns the turn's content hash."""
        from .session import content_hash
        h = content_hash(text)
        if h in session.digested:
            return h
        session.digested.append(h)
        session.originals.setdefault(h, text)
        folded = fold_turn("", text, session, max_lines=2)
        seen_lines = {e["line"].split(": ", 1)[-1] for e in session.digest_entries}
        for ln in folded.splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("[+"):
                continue
            # terse-compress the line (protected regions lifted/restored so
            # refs, paths and numbers stay byte-exact)
            try:
                skel, regs = protected.extract(ln)
                ln = protected.restore(phrases.compress(skel), regs).strip()
            except Exception:
                pass
            if len(ln) > 240:               # word-boundary truncation
                cut = ln.rfind(" ", 0, 237)
                ln = ln[:cut if cut > 120 else 237] + "..."
            if not ln or ln in seen_lines:  # dedupe on the final stored form
                continue
            seen_lines.add(ln)
            session.digest_entries.append(
                {"h": h, "line": f"{role[0]}{len(session.digested)}: {ln}"})
        if len(session.digest_entries) > 200:
            session.digest_entries = session.digest_entries[-200:]
        return h

    def _fold_old_turn(self, text: str, session: Session, idx: int,
                       hist_tokens: int) -> tuple[str, str, str]:
        """Returns (noalias_fold, alias_fold, rep)."""
        deduped = dedup_blob(text, session)
        if deduped != text:
            return deduped, deduped, "fold"
        base = None
        for f in session.folds:   # abstractive fold available?
            if f.start <= idx <= f.end and f.kind == "abstractive":
                base = f.summary
                break
        if base is None:
            max_lines = 3 if self.cfg.mode == "MAX" else 6
            base = fold_turn("", text, session, max_lines=max_lines)
        aliased = self._alias_pass(base, session)
        if (self.folder and hist_tokens >= self.cfg.abstractive_threshold_tokens
                and len(text) > 800):
            self.folder.maybe_fold(session, idx, idx, base)
        return base, aliased, "fold"

    # ------------------------------------------------------------------
    def _compress_new(self, text: str, role: str, prof,
                      session: Session) -> tuple[str, str, str, float, list[str]]:
        """Returns (noalias_best, alias_best, rep, confidence, failures)."""
        cfg = self.cfg
        try:
            skel, regions = protected.extract(text)
        except Exception:
            return text, text, "original", 1.0, ["protect-failed"]

        cands: list[tuple[str, str]] = []
        terse_skel = phrases.compress(skel)
        cands.append((terse_skel, "terse"))

        alias_skel = terse_skel
        subbed = False
        for pat, code in self.dict.substitutable():
            alias_skel, k = pat.subn(code, alias_skel)
            subbed = subbed or k > 0
        if subbed:
            # collapse known K-sequences into policy bundle codes (P*)
            from .seed import bundle_patterns
            import re as _re
            for bp, pcode in bundle_patterns():
                alias_skel = _re.sub(bp, pcode, alias_skel)
        for name, code in session.entity_pairs():
            if name in alias_skel:
                # ENT block defines codes on every request: alias all occurrences
                alias_skel = alias_skel.replace(name, code)
                subbed = True
        if subbed:
            cands.append((alias_skel, "terse+alias"))

        expansions = self.dict.expansions()
        orig_tok = prof.count(text)
        noalias_best = alias_best = text
        best_rep, best_conf, best_fails = "original", 1.0, []
        noalias_tok = alias_tok = orig_tok
        for skel_c, rep in cands:
            try:
                restored = protected.restore(skel_c, regions)
            except Exception:
                continue
            decoded = self._expand_codes(restored, expansions, session)
            v = verifier.verify(text, decoded)
            if not v.ok or v.confidence < cfg.min_confidence:
                continue
            tok = prof.count(restored)
            saved = orig_tok - tok
            if not (saved >= cfg.min_savings_abs
                    or saved / max(1, orig_tok) * 100 >= cfg.min_savings_pct):
                continue
            if rep == "terse" and tok < noalias_tok:
                noalias_best, noalias_tok = restored, tok
                # terse also improves the alias track if aliasing never fired
                if tok < alias_tok:
                    alias_best, alias_tok = restored, tok
                    best_rep, best_conf, best_fails = rep, v.confidence, v.failures
            elif rep == "terse+alias" and tok < alias_tok:
                alias_best, alias_tok = restored, tok
                best_rep, best_conf, best_fails = rep, v.confidence, v.failures

        # learning: feed recurring clauses to the nursery. Semicolon-only splitting
        # means a phrase can never be recognized as recurring content unless it's
        # written as a semicolon-separated imperative (this project's own documented
        # target style, docs/foldlang.md: "Clauses separated by ;") -- a consumer
        # whose real repeated boilerplate is normal grammatical prose (complete
        # sentences, no semicolons at all) gets zero observations no matter how many
        # times that exact text repeats. Confirmed live (agent-manager integration,
        # 2026-08-21): 40 identical repeats of a real ~1500-char instructional block in
        # one session, nursery count never moved from its starting value. Adding
        # sentence-boundary splitting alongside the semicolon split (same regex shape
        # folding.py's own fold_turn() already uses for a related purpose) lets a real
        # repeated SENTENCE be observed too, not just a semicolon-joined clause.
        if role in ("user", "system"):
            for clause in [c.strip() for c in _CLAUSE_SPLIT.split(terse_skel) if len(c.strip()) > 15]:
                if "⟦" not in clause:
                    self.dict.observe(clause, prof.count(clause))
            self.dict.save()

        return noalias_best, alias_best, best_rep, best_conf, best_fails

    # ------------------------------------------------------------------
    _CODE_TOKEN_RE = re.compile(r"\b[KPE]\d+\b")

    # Bug (found live, agent-manager integration 2026-08-21): the old version replaced
    # codes one at a time with str.replace(), first for K/P codes (dict iteration =
    # insertion/mint order, so short codes always come first) then for E-codes in a
    # SEPARATE second loop. Either loop corrupts output the moment its code family holds
    # more than 9 entries: "K4" is a literal substring of "K40", "K400", etc. (same for
    # "E1" inside "E10"), so an early replace mangles every later, longer code sharing
    # that prefix before its own turn comes -- "K400" becomes "<K4's expansion>00", not
    # "<K400's expansion>". Confirmed live for K-codes: a real request decoded to
    # unrelated seed-phrase text with stray digit fragments ("...after changes00
    # preserve existing behavior82..."), caught by the verifier and correctly discarded
    # (no corrupted prompt reached a model), but it silently zeroed the alias mechanism's
    # entire benefit for any dictionary bigger than single digits -- the normal size
    # after even light real use. E-codes have the identical latent bug, just not yet hit
    # in practice because a session accumulates entities slower than dictionary codes.
    # Decoder.decode() (decoder.py) already handles K/P/E codes correctly with one
    # word-bounded regex pass; this merges entity names into the same expansions map and
    # reuses that exact approach instead of two divergent, both-broken loops.
    @staticmethod
    def _expand_codes(text: str, expansions: dict[str, str], session: Session) -> str:
        if session.entities:
            expansions = {**expansions, **{e.code: e.name for e in session.entities.values()}}
        return Encoder._CODE_TOKEN_RE.sub(
            lambda m: expansions.get(m.group(0), m.group(0)), text)

    def _codes_used(self, msgs: list[dict]) -> bool:
        from .dictionary import find_codes
        est = self.dict.established_codes()
        return any(c in est for m in msgs for c in find_codes(self._text(m)))

    @staticmethod
    def _entity_codes_used(msgs: list[dict], session: Session) -> bool:
        from .dictionary import find_codes
        codes = set(session.entities.keys())
        return any(c in codes
                   for m in msgs for c in find_codes(Encoder._text(m)))
