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
        folder = None
        if self.cfg.fold_history:
            folder = AbstractiveFolder(self.cfg.ollama_url, self.cfg.tiny_model)
        self.encoder = Encoder(self.cfg, self.dict, folder)
        self.metrics = Metrics()

    # ------------------------------------------------------------------
    def encode(self, messages: list[dict], model: str,
               provider: str = "",
               session_id: str | None = None) -> tuple[list[dict], EncodeReport]:
        cache_key = None
        if self.cfg.mode != "OFF" and len(messages) == 1:
            basis = json.dumps([messages, model, self.cfg.mode,
                                len(self.dict.generations)], ensure_ascii=False)
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
        out, report = self.encoder.encode(messages, model, session_id=session_id)
        self.metrics.record(report, direction="in", provider=provider,
                            project=self.cfg.scope)
        # opportunistic dictionary learning: try minting a new generation
        # every 25 encodes (cheap no-op when nothing clears the bar)
        self._since_mint = getattr(self, "_since_mint", 0) + 1
        if self._since_mint >= 25:
            self._since_mint = 0
            try:
                self.dict.mint()
            except Exception:
                pass
        if cache_key and not report.fallback:
            self.metrics.cache_put(cache_key, json.dumps(out, ensure_ascii=False),
                                   "encoded")
        return out, report

    # ------------------------------------------------------------------
    def decoder_for(self, session_id: str) -> Decoder:
        session = Session(session_id) if session_id and session_id != "cache" else None
        return Decoder(self.dict, session)

    def decode(self, text: str, session_id: str = "") -> str:
        if self.cfg.route_mode == "agent":
            return text                      # Agent Mode: stay compact
        try:
            return self.decoder_for(session_id).decode(text)
        except Exception:
            return text                      # fail-soft

    def stream_decoder(self, session_id: str = "") -> StreamDecoder:
        return StreamDecoder(self.decoder_for(session_id))

    # ------------------------------------------------------------------
    def record_output(self, model: str, generated: str, decoded: str,
                      session_id: str = "") -> None:
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
            self.metrics.record(rep, direction="out", project=self.cfg.scope)
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
