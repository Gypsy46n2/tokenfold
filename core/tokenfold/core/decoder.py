"""Inbound decoder: model output -> human text (Human Mode) or passthrough
compact form (Agent Mode).

Expansion is token-local (K/P/E codes and [ref:...] / [code:...] markers), so
it streams: StreamDecoder buffers only until a code candidate is complete
(a word boundary), then flushes. Unknown codes are left verbatim (fail-soft:
never invent an expansion).
"""

from __future__ import annotations

import re
from typing import Iterator, Optional

from .dictionary import Dictionary
from .session import Session

_CODE = re.compile(r"\b([KPE]\d+)\b")
_REF = re.compile(r"\[(?:ref|code):([0-9a-f]{16})[^\]]*\]")
# a partial code possibly still growing at the end of the buffer
_TAIL_RISK = re.compile(r"(?:[KPE]\d*|\[(?:r(?:e(?:f)?)?|c(?:o(?:de?)?)?)?[^\]]*)$")


class Decoder:
    def __init__(self, dictionary: Dictionary, session: Optional[Session] = None):
        self.expansions = dictionary.expansions()
        self.session = session
        if session:
            for e in session.entities.values():
                self.expansions.setdefault(e.code, e.name)

    def decode(self, text: str) -> str:
        out = _CODE.sub(lambda m: self.expansions.get(m.group(1), m.group(1)), text)
        if self.session:
            out = _REF.sub(self._ref_sub, out)
        return out

    def _ref_sub(self, m: re.Match) -> str:
        orig = self.session.originals.get(m.group(1))
        return orig if orig is not None else m.group(0)


class StreamDecoder:
    """Incremental decoder for SSE deltas. feed() returns decodable prefix;
    flush() returns whatever remains."""

    def __init__(self, decoder: Decoder):
        self.d = decoder
        self.buf = ""

    def feed(self, delta: str) -> str:
        self.buf += delta
        m = _TAIL_RISK.search(self.buf)
        safe_end = m.start() if m else len(self.buf)
        out, self.buf = self.buf[:safe_end], self.buf[safe_end:]
        return self.d.decode(out) if out else ""

    def flush(self) -> str:
        out, self.buf = self.buf, ""
        return self.d.decode(out) if out else ""


def decode_stream(chunks: Iterator[str], decoder: Decoder) -> Iterator[str]:
    sd = StreamDecoder(decoder)
    for c in chunks:
        piece = sd.feed(c)
        if piece:
            yield piece
    tail = sd.flush()
    if tail:
        yield tail
