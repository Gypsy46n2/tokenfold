"""History folding: reclaim context from old turns.

Extractive (always, deterministic, cache-stable):
  * old assistant/user turns reduced to key lines (decisions, errors,
    constraints, questions, headers) via rule scoring
  * code fences replaced with [code:<hash> <n> lines] references
  * repeated blobs replaced with [ref:<hash>] after first occurrence

Abstractive (optional, background, tiny local model):
  * spans of extractively-folded turns get a 1-3 sentence summary from a
    small Ollama model, computed OFF the request path; until ready, the
    extractive fold is used. Folds are append-only: once minted, a fold's
    text never changes (prefix-cache safety).

Originals are retained in the session store; a fold always carries its
hashes so content can be re-expanded on demand.
"""

from __future__ import annotations

import re
import threading
from typing import Callable, Optional

from .session import Session, content_hash

_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.S)
_KEY_LINE = re.compile(
    r"\b(error|fail(?:ed|ure)?|exception|traceback|decided?|decision|must|"
    r"should not|do not|don't|constraint|requirement|warning|fixed|bug|"
    r"todo|important|note:|conclusion|result)\b", re.I)
_HEADER = re.compile(r"^\s{0,3}#{1,6}\s|^\s*[-*]\s|^\s*\d+\.\s")


def fold_code_fences(text: str, session: Session) -> str:
    """Replace code fences with stable references; store originals."""
    def _sub(m: re.Match) -> str:
        body = m.group(0)
        h, seen = session.note_blob(body, "code")
        lines = body.count("\n")
        return f"[code:{h} {lines}L{' seen' if seen else ''}]"
    return _FENCE.sub(_sub, text)


def dedup_blob(text: str, session: Session, min_len: int = 400) -> str:
    """If this exact blob appeared before in the session, refer to it."""
    if len(text) < min_len:
        return text
    h = content_hash(text)
    if h in session.seen_blobs:
        kind = session.seen_blobs[h]["kind"]
        return f"[ref:{h} {kind} unchanged from turn {session.seen_blobs[h]['turn']}]"
    return text


_SENT = re.compile(r"(?<=[.!?])\s+")


def extract_key_lines(text: str, max_lines: int = 6) -> str:
    """Deterministic extractive summary: first line + scored key lines.
    Single-line paragraphs are split into sentences so prose folds too."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= max_lines and len(text) > 200:
        # long prose with few newlines: treat sentences as lines
        lines = [s.strip() for ln in lines for s in _SENT.split(ln) if s.strip()]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    scored: list[tuple[int, int, str]] = []
    for i, ln in enumerate(lines):
        s = 0
        if i == 0 or i == len(lines) - 1:
            s += 2
        if _KEY_LINE.search(ln):
            s += 3
        if _HEADER.match(ln):
            s += 1
        if len(ln) > 200:
            s -= 1
        scored.append((s, i, ln))
    keep = sorted(sorted(scored, key=lambda t: -t[0])[:max_lines], key=lambda t: t[1])
    picked = [ln if len(ln) <= 240 else ln[:237] + "..." for _, _, ln in keep]
    dropped = len(lines) - len(picked)
    return "\n".join(picked) + (f"\n[+{dropped} lines folded]" if dropped > 0 else "")


def fold_turn(role: str, text: str, session: Session,
              max_lines: int = 6) -> str:
    """Extractive fold of one old turn."""
    t = fold_code_fences(text, session)
    t = extract_key_lines(t, max_lines=max_lines)
    return t


class AbstractiveFolder:
    """Background summarizer using a tiny Ollama model. Fire-and-forget:
    request path never waits on it. Results land in session.folds and are
    used from the *next* request onward (append-only)."""

    def __init__(self, ollama_url: str, model: str,
                 http_post: Optional[Callable] = None):
        self.url = ollama_url.rstrip("/")
        self.model = model
        self._post = http_post          # injectable for tests
        self._inflight: set[str] = set()

    def maybe_fold(self, session: Session, start: int, end: int,
                   folded_text: str) -> None:
        key = f"{session.id}:{start}-{end}"
        if key in self._inflight:
            return
        if any(f.start == start and f.end == end and f.kind == "abstractive"
               for f in session.folds):
            return
        self._inflight.add(key)
        threading.Thread(target=self._work, daemon=True,
                         args=(session, start, end, folded_text, key)).start()

    def maybe_compact_digest(self, session: Session, window: int) -> None:
        """When the digest store grows well past the render window, squeeze
        the oldest lines into fewer, denser ones with the tiny model.
        Runs in the background; entries are replaced atomically on success,
        keeping the union of their source-turn hashes for scoping."""
        if len(session.digest_entries) <= 2 * window:
            return
        key = f"compact:{session.id}:{len(session.digest_entries)}"
        if key in self._inflight:
            return
        self._inflight.add(key)
        threading.Thread(target=self._compact_work, daemon=True,
                         args=(session, window, key)).start()

    def _compact_work(self, session: Session, window: int, key: str) -> None:
        try:
            import httpx
            old = session.digest_entries[:-window]
            text = "\n".join(e["line"] for e in old)
            prompt = ("Compress this conversation log into at most 4 terse "
                      "lines. Keep every decision, constraint, number, "
                      "filename, error and [code:...]/[ref:...] reference "
                      "verbatim. No preamble.\n\n" + text[:5000])
            post = self._post or (lambda url, json: httpx.post(url, json=json, timeout=90))
            r = post(f"{self.url}/api/generate", json={
                "model": self.model, "prompt": prompt, "stream": False,
                "options": {"temperature": 0, "num_predict": 200}})
            summary = (r.json() or {}).get("response", "").strip()
            lines = [ln.strip() for ln in summary.splitlines() if ln.strip()][:4]
            if lines:
                hashes = sorted({h for e in old
                                 for h in (e.get("hs") or [e.get("h")]) if h})
                fresh = [{"hs": hashes, "line": ln} for ln in lines]
                # re-read to avoid clobbering concurrent updates, then swap
                cur = Session(session.id)
                if len(cur.digest_entries) >= len(session.digest_entries):
                    cur.digest_entries = fresh + cur.digest_entries[len(old):]
                    cur.save()
        except Exception:
            pass
        finally:
            self._inflight.discard(key)

    def _work(self, session: Session, start: int, end: int,
              text: str, key: str) -> None:
        try:
            import httpx
            prompt = ("Summarize this conversation excerpt in <=3 terse sentences. "
                      "Keep every decision, constraint, number, filename and error "
                      "verbatim. No preamble.\n\n" + text[:6000])
            post = self._post or (lambda url, json: httpx.post(url, json=json, timeout=60))
            r = post(f"{self.url}/api/generate", json={
                "model": self.model, "prompt": prompt, "stream": False,
                "options": {"temperature": 0, "num_predict": 160}})
            summary = (r.json() or {}).get("response", "").strip()
            if summary:
                from .session import Fold
                session.folds.append(Fold(start=start, end=end, summary=summary,
                                          kind="abstractive"))
                session.save()
        except Exception:
            pass                          # extractive fold remains in use
        finally:
            self._inflight.discard(key)
