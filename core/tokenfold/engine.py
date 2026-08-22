"""Engine facade: the one object adapters talk to.

    eng = Engine()                       # loads config, dictionary, metrics
    msgs, report = eng.encode(messages, model)
    text = eng.decode(text, session_id)  # or eng.stream_decoder(session_id)
"""

from __future__ import annotations

import hashlib
import json
from typing import Optional

from .core.config import Config, load as load_config
from .core.decoder import Decoder, StreamDecoder
from .core.dictionary import Dictionary
from .core.encoder import Encoder, EncodeReport
from .core.folding import AbstractiveFolder
from .core.metrics import Metrics
from .core.session import Session


class Engine:
    def __init__(self, cfg: Optional[Config] = None):
        self.cfg = (cfg or load_config()).clamp()
        self.dict = Dictionary(scope=self.cfg.scope)
        if self.cfg.seed_dictionary and not self.dict.generations:
            from .core.seed import seed
            seed(self.dict)
        self.folder = None
        if self.cfg.fold_history:
            self.folder = AbstractiveFolder(self.cfg.ollama_url, self.cfg.tiny_model)
        self.encoder = Encoder(self.cfg, self.dict, self.folder)
        self.metrics = Metrics()
        # Per-task-type dictionaries (agent-manager integration, 2026-08-21,
        # Grimmethy: "Each job type could have it's own folded dictionary"). The
        # module docstring's "global / per-project / per-model, layered" scope design
        # already existed on Dictionary (one JSON file per scope name) but nothing ever
        # actually used more than one scope at a time -- every request shared the single
        # self.dict/self.encoder above regardless of caller. A caller whose traffic is
        # actually many DIFFERENT template shapes glued into one session (this
        # integration's real shape: one worker-lane session serving arch_discovery,
        # observability_review, performance_review, etc. calls back to back) dilutes
        # every one of those templates' real repetition into a single shared dictionary,
        # and -- see encoder.py's glossary-scoping fix from the same date -- makes any
        # one call's relevant-code glossary a lottery of whichever OTHER task type
        # happened to establish codes first. Scoping by task type instead lets each
        # template's own highly consistent boilerplate build its own dictionary against
        # only its own repetition, undiluted. Lazily created/seeded on first use, kept
        # for the engine's lifetime; self.dict/self.encoder above remain the "no scope
        # given" fallback for every existing caller (CLI, hooks, tests) that never knew
        # scope was a per-request concept at all.
        self._scoped_encoders: dict[str, Encoder] = {}
        self._since_mint: dict[str, int] = {}

    def _encoder_for(self, scope: str | None) -> Encoder:
        if not scope or scope == self.cfg.scope:
            return self.encoder
        enc = self._scoped_encoders.get(scope)
        if enc is None:
            d = Dictionary(scope=scope)
            if self.cfg.seed_dictionary and not d.generations:
                from .core.seed import seed
                seed(d)
            enc = Encoder(self.cfg, d, self.folder)
            self._scoped_encoders[scope] = enc
        return enc

    # ------------------------------------------------------------------
    def encode(self, messages: list[dict], model: str,
               provider: str = "",
               session_id: str | None = None,
               scope: str | None = None) -> tuple[list[dict], EncodeReport]:
        enc = self._encoder_for(scope)
        cache_key = None
        if self.cfg.mode != "OFF" and len(messages) == 1:
            # Bug (found live, agent-manager integration 2026-08-21): the cache key
            # never included session_id, so a cache hit could replay a DIFFERENT
            # session's cached response for byte-identical single-message content --
            # skipping self.encoder.encode() entirely on a hit also skips session.save()
            # (turn/used_codes never advance for the session that got served the cached
            # copy). Confirmed live: repeated identical prompts across separate calls
            # returned session_id="cache" every time, silently starving that session's
            # own turn counter of the increments the session-continuity mechanism
            # depends on. Including session_id makes a hit only ever replay THIS
            # session's own prior response, which is the only case where skipping
            # encode() (and its session-state update) is actually correct. scope is
            # included for the same reason: two different scopes must never share a
            # cache entry, since they can decode the identical-looking encoded body
            # against two completely different dictionaries.
            basis = json.dumps([messages, model, self.cfg.mode, session_id, scope,
                                len(enc.dict.generations)], ensure_ascii=False)
            cache_key = hashlib.sha256(basis.encode()).hexdigest()
            hit = self.metrics.cache_get(cache_key)
            if hit:
                encoded, rep = hit
                try:
                    out = json.loads(encoded)
                    report = EncodeReport(session_id="cache", model=model,
                                          profile="cache", exact_tokenizer=True)
                    return out, report
                except Exception:
                    pass
        out, report = enc.encode(messages, model, session_id=session_id, provider=provider)
        self.metrics.record(report, direction="in", provider=provider,
                            project=scope or self.cfg.scope)
        # opportunistic dictionary learning: try minting a new generation every 25
        # encodes PER SCOPE (a shared counter would let one scope's traffic volume
        # starve another's mint cadence, or trigger it on the wrong dictionary).
        mint_key = scope or self.cfg.scope
        self._since_mint[mint_key] = self._since_mint.get(mint_key, 0) + 1
        if self._since_mint[mint_key] >= 25:
            self._since_mint[mint_key] = 0
            try:
                enc.dict.mint()
            except Exception:
                pass
        if cache_key and not report.fallback:
            self.metrics.cache_put(cache_key, json.dumps(out, ensure_ascii=False),
                                   "encoded")
        return out, report

    # ------------------------------------------------------------------
    def decoder_for(self, session_id: str, scope: str | None = None) -> Decoder:
        session = Session(session_id) if session_id and session_id != "cache" else None
        # Must resolve the SAME scope's dictionary the response was encoded against --
        # K1/K2/... mean completely different things in different scopes, so decoding
        # against the wrong one wouldn't just miss a code, it could expand it as the
        # WRONG phrase entirely.
        return Decoder(self._encoder_for(scope).dict, session)

    def decode(self, text: str, session_id: str = "", scope: str | None = None) -> str:
        if self.cfg.route_mode == "agent":
            return text                      # Agent Mode: stay compact
        try:
            return self.decoder_for(session_id, scope).decode(text)
        except Exception:
            return text                      # fail-soft

    def stream_decoder(self, session_id: str = "", scope: str | None = None) -> StreamDecoder:
        return StreamDecoder(self.decoder_for(session_id, scope))

    # ------------------------------------------------------------------
    def record_output(self, model: str, generated: str, decoded: str,
                      session_id: str = "", scope: str | None = None) -> None:
        """Metrics for the response side: tokens the model actually
        generated vs the expanded text the user reads. Expansion is local
        and token-free, so decoded - generated = output tokens saved."""
        try:
            from .tokenizers.registry import profile_for
            from .core.encoder import EncodeReport
            prof = profile_for(model)
            rep = EncodeReport(session_id=session_id, model=model,
                               profile=prof.name, exact_tokenizer=prof.exact)
            rep.original_tokens = prof.count(decoded)
            rep.encoded_tokens = prof.count(generated)
            self.metrics.record(rep, direction="out", project=scope or self.cfg.scope)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def expansion_requests(self, response_text: str, session_id: str) -> list[tuple[str, str]]:
        """Hashes the model asked to expand, with their stored originals."""
        import re
        from .core.session import Session
        if not session_id or session_id == "cache":
            return []
        try:
            session = Session(session_id)
            # any line that both mentions "expand" and carries a ref hash is
            # treated as an expansion request (order-independent)
            found: list[str] = []
            for line in response_text.splitlines():
                if re.search(r"\bexpand", line, re.I):
                    found += re.findall(
                        r"\[?(?:code|ref):\s*([0-9a-f]{16})", line, re.I)
            out = []
            for h in dict.fromkeys(found):
                if h in session.originals:
                    out.append((h, session.originals[h]))
                    if h not in session.pending_expansions:
                        session.pending_expansions.append(h)
            if out:
                session.save()
            return out
        except Exception:
            return []
