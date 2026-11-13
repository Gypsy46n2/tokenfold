### AC-1 · Decompose Long CLI Main
Strength: Strong
Files: core/tokenfold/cli.py
Symbol: main
Snippet:
```


def main(argv: list[str] | None = None) -> int:
    # under pythonw.exe there is no console: make print() a safe no-op
    if sys.stdout is None or sys.stderr is None:
        import io
        sys.stdout = sys.stdout or io.StringIO()
        sys.stderr = sys.stderr or io.StringIO()
    p = argparse.ArgumentParser(prog="tokenfold")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve")
    sp.add_argument("--port", type=int, default=9339)
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--upstream", default=None)

    sub.add_parser("stats")
    sub.add_parser("bench")

    ep = sub.add_parser("encode")
    ep.add_argument("text")
    ep.add_argument("--model", default="gpt-4o")

    dp = sub.add_parser("dict")
    dp.add_argument("action", choices=["list", "export", "import", "mint"],
                    nargs="?", default="list")
    dp.add_argument("file", nargs="?")

    cp = sub.add_parser("config")
    cp.add_argument("action", choices=["get", "set"])
    cp.add_argument("key", nargs="?")
    cp.add_argument("value", nargs="?")

    a = p.parse_args(argv)

    if a.cmd == "serve":
        import uvicorn
        from .adapters.proxy import create_app
        from .engine import Engine
        eng = Engine()
        if a.upstream:
            eng.cfg.upstream = a.upstream
        print(f"TokenFold proxy on http://{a.host}:{a.port}/v1 "
              f"-> upstream {eng.cfg.upstream}  (mode={eng.cfg.mode})")
        print(f"dashboard: http://{a.host}:{a.port}/tokenfold/dashboard")
        uvicorn.run(create_app(eng), host=a.host, port=a.port, log_level="warning")
        return 0

    if a.cmd == "stats":
        from .core.metrics import Metrics
        print(Metrics().dump_json())
        return 0

    if a.cmd == "bench":
        import subprocess
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent
        return subprocess.call([sys.executable, str(root / "benchmarks" / "run_phase1.py")])

    if a.cmd == "encode":
        from .engine import Engine
        eng = Engine()
        msgs, rep = eng.encode([{"role": "user", "content": a.text}], a.model)
        print(json.dumps({"encoded": msgs, "original_tokens": rep.original_tokens,
                          "encoded_tokens": rep.encoded_tokens,
                          "dictionary_overhead": rep.dictionary_overhead,
                          "saved": rep.saved, "pct": round(rep.pct, 1),
                          "latency_ms": round(rep.latency_ms, 2),
                          "fallback": rep.fallback},
                         indent=1, ensure_ascii=False))
        return 0

    if a.cmd == "dict":
        from .core.dictionary import Dictionary
        d = Dictionary()
        if a.action == "list":
            for c, al in sorted(d.aliases.items()):
                print(f"{c:5s} = {al.expansion}  (uses={al.uses})")
            gen = d.active_generation
            print(f"\ngeneration: {gen.version if gen else 0}; "
                  f"nursery: {len(d.nursery)} candidates")
        elif a.action == "export":
            print(d.export_json())
        elif a.action == "import" and a.file:
            d.import_json(open(a.file, encoding="utf-8").read())
            print("imported")
        elif a.action == "mint":
            gen = d.mint()
            print(f"minted generation {gen.version}" if gen
                  else "nothing promotable (net savings would not clear the bar)")
        return 0

    if a.cmd == "config":
        from .core import config as cfgmod
        cfg = cfgmod.load()
        if a.action == "get":
            print(json.dumps(cfg.__dict__, indent=1))
        else:
            if not a.key or a.value is None:
                print("usage: tokenfold config set KEY VALUE")
                return 2
            cur = getattr(cfg, a.key, None)
            if cur is None:
                print(f"unknown key {a.key}")
                return 2
            typ = type(cur)
            val = (a.value.lower() in ("1", "true", "yes")) if typ is bool \
                else typ(a.value)
            setattr(cfg, a.key, val)
            cfgmod.save(cfg.clamp())
            print(f"{a.key} = {val}")
        return 0

    return 1
```

Problem:
The `main` function is 112 lines long and handles six distinct CLI command branches (`serve`, `stats`, `bench`, `encode`, `dict`, `config`), each with its own argument parsing, logic, and side effects. This violates the Single Responsibility Principle, making the function hard to test, debug, and extend. The length increases cognitive load and makes refactoring risky, especially as new commands or logic are added.

Solution:
Extract `parse_args` into its own function. Then extract each command handler into its own function: `handle_serve`, `handle_stats`, `handle_bench`, `handle_encode`, `handle_dict`, `handle_config`. Wrap the command dispatch in a `dispatch` function that routes based on `a.cmd`. The top-level `main` function will then be minimal — just parse args, dispatch, and return exit code — reducing its length to ~20–30 lines.

Benefits:
Each extracted function has a single, clear responsibility and can be tested independently. Changes to one command won’t risk breaking others. The code becomes more readable, maintainable, and easier to review — especially for new contributors or during refactoring.

VERDICT CONSTRAINT:
Genuine

### AC-2 · Chat Function Decomposition
Strength: Strong
Files: core/tokenfold/adapters/proxy.py
Symbol: chat
Snippet:
```
    # -----------------------------------------------------------------
    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        try:
            body = await request.json()
        except Exception:
            raw = await request.body()
            r = await client.post(f"{_upstream(request)}/chat/completions",
                                  content=raw, headers=_fwd_headers(request))
            return Response(r.content, r.status_code)

        mode_hdr = request.headers.get("x-tokenfold-mode")
        route_hdr = request.headers.get("x-tokenfold-route")
        if mode_hdr:
            eng.cfg.mode = mode_hdr.upper()
            eng.cfg.clamp()
        if route_hdr:
            eng.cfg.route_mode = route_hdr.lower()
            eng.cfg.clamp()

        model = body.get("model", "")
        messages = body.get("messages", [])
        sid_hdr = request.headers.get("x-tokenfold-session")
        scope_hdr = request.headers.get("x-tokenfold-scope")
        encoded, report = eng.encode(messages, model, session_id=sid_hdr, scope=scope_hdr)
        body["messages"] = encoded
        sid = report.session_id

        upstream = f"{_upstream(request)}/chat/completions"
        headers = _fwd_headers(request)
        headers["content-type"] = "application/json"

        if body.get("stream"):
            async def gen() -> AsyncIterator[bytes]:
                sd = eng.stream_decoder(sid, scope=scope_hdr)
                raw_acc, dec_acc = [], []
                async with client.stream("POST", upstream, json=body, timeout=STREAM_TIMEOUT,
                                         headers=headers) as r:
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            if line.strip():
                                yield (line + "\n").encode()
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            tail = sd.flush()
                            if tail:
                                dec_acc.append(tail)
                                yield _sse_delta(tail)
                            raw, dec = "".join(raw_acc), "".join(dec_acc)
                            eng.record_output(model, raw, dec, sid, scope=scope_hdr)
                            # streamed: can't retry, but note the ask so the
                            # NEXT request ships the original automatically
                            eng.expansion_requests(raw, sid)
                            yield b"data: [DONE]\n\n"
                            continue
                        try:
                            obj = json.loads(payload)
                            delta = obj["choices"][0]["delta"].get("content")
                        except Exception:
                            yield (line + "\n\n").encode()
                            continue
                        if delta:
                            raw_acc.append(delta)
                            piece = sd.feed(delta)
                            if piece:
                                dec_acc.append(piece)
                                obj["choices"][0]["delta"]["content"] = piece
                                yield ("data: " + json.dumps(obj) + "\n\n").encode()
                            # swallowed partial: emitted once boundary is safe
                        else:
                            yield (line + "\n\n").encode()
            return StreamingResponse(gen(), media_type="text/event-stream")

        r = await client.post(upstream, json=body, headers=headers)
        try:
            obj = r.json()
            raw0 = ((obj.get("choices") or [{}])[0].get("message") or {}).get("content")
            # auto re-expansion: model asked to see a folded ref -> inject the
            # original and retry ONCE before answering the user
            if isinstance(raw0, str):
                asks = eng.expansion_requests(raw0, sid)
                if asks:
                    supplement = "\n\n".join(
                        f"[expanded {h}]\n{orig}" for h, orig in asks[:3])
                    retry = dict(body)
                    retry["messages"] = list(body["messages"]) + [
                        {"role": "assistant", "content": raw0},
                        {"role": "user", "content":
                         "Expanded content you requested:\n" + supplement +
                         "\n\nNow answer the previous question."}]
                    r2 = await client.post(upstream, json=retry, headers=headers)
                    try:
                        obj = r2.json()
                    except Exception:
                        pass
            for ch in obj.get("choices", []):
                msg = ch.get("message", {})
                if isinstance(msg.get("content"), str):
                    raw = msg["content"]
                    msg["content"] = eng.decode(raw, sid, scope=scope_hdr)
                    eng.record_output(model, raw, msg["content"], sid, scope=scope_hdr)
            return JSONResponse(obj, status_code=r.status_code)
        except Exception:
            return Response(r.content, r.status_code)
```

Problem:
The `chat` function is 103 lines long and performs at least six distinct responsibilities: HTTP body parsing with fallbacks, header-based engine configuration, message encoding with session/scope injection, streaming response generation with stateful delta management, non-streaming POST with retry logic for expanded content, and final response construction with decoding and output recording. These responsibilities have different failure modes, test requirements, and side effects — for example, streaming logic is stateful and error-prone while retry logic modifies request state independently. Keeping them all in one function creates cognitive overload, reduces test isolation, and makes incremental changes risky.

Solution:
Extract six logical pieces into separate async functions: `_parse_request_body`, `_configure_engine_from_headers`, `_extract_model_and_messages`, `_handle_streaming_request`, `_handle_non_streaming_request`, and `_record_output`. Each function should handle one responsibility, with clear inputs and outputs. The main `chat` function becomes a coordinator that orchestrates these pieces based on the `stream` flag, with no internal logic beyond routing and error handling.

Benefits:
Decomposition improves readability by isolating concerns, enables focused unit tests for each subfunction (e.g., test streaming deltas without touching retry logic), and allows independent evolution — for example, changing streaming behavior won’t require touching retry or config logic. It also reduces the risk of introducing bugs during refactoring or debugging by limiting scope.

VERDICT CONSTRAINT:
- The function snippet IS provided above in this prompt; you do not need to request it or assume it is missing.
- You MUST render exactly one verdict: GENUINE or FALSE POSITIVE.
- Refusals, hedging, or a third outcome (including "Uncertain", "I cannot determine", or any other qualifier) are NOT acceptable. Pick the one verdict that best fits the evidence in the snippet and commit to it.

VERDICT: GENUINE

### AC-3 · Decompose Monolithic Encoder
Strength: Strong
Files: core/tokenfold/core/encoder.py
Symbol: _encode
Snippet:
```

    # ------------------------------------------------------------------
    def _encode(self, messages: list[dict], model: str, prof,
                report: EncodeReport, t0: float,
                session_id: str | None = None,
                provider: str = "") -> tuple[list[dict], EncodeReport]:
        cfg = self.cfg
        sid = session_id or session_id_for(messages)
        report.session_id = sid
        session = Session(sid)
        session.turn += 1

        if cfg.mode == "OFF":
            report.original_tokens = report.encoded_tokens = self._count_all(messages, prof)
            report.latency_ms = (time.perf_counter() - t0) * 1000
            return messages, report

        # Tiny requests can never clear the min-savings thresholds: the full
        # pipeline runs only to have the never-larger invariant revert it,
        # which burned latency AND stamped fallback=True — live metrics showed
        # a cluster of 5–35 token requests inflating fallback_pct with what is
        # really just correct "nothing to do here" behavior. Skip early.
        total_tok = self._count_all(messages, prof)
        if total_tok < cfg.min_encode_tokens:
            # Learning must NOT be gated with the pipeline: short boilerplate
            # repeated across many tiny requests is exactly what the nursery
            # exists to notice. Same observe step the candidate search runs.
            for m in messages:
                if m.get("role") in ("user", "system"):
                    try:
                        skel, _regs = protected.extract(self._text(m))
                        terse = phrases.compress(skel)
                    except Exception:
                        continue
                    for clause in [c.strip() for c in _CLAUSE_SPLIT.split(terse)
                                   if len(c.strip()) > 15]:
                        if "⟦" not in clause:
                            self.dict.observe(clause, prof.count(clause))
            self.dict.save()
            report.original_tokens = report.encoded_tokens = total_tok
            report.messages.append(MessageReport(
                index=0, representation="small-passthrough",
                original_tokens=total_tok, encoded_tokens=total_tok))
            report.latency_ms = (time.perf_counter() - t0) * 1000
            session.save()          # the turn increment above must still stick
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
            # An assistant message carrying tool_calls must NEVER be folded away:
            # its paired role:"tool" message (kept as a [ref:...] below) has a
            # tool_call_id that the API requires be introduced by a preceding
            # assistant tool_calls. Omit the assistant turn and the upstream
            # 400s the whole request. Keep it verbatim (these turns are short).
            if is_old and m.get("tool_calls"):
                out.append(m)
                out_noalias.append(m)
                report.encoded_tokens += orig_tok
                report.messages.append(MessageReport(
                    index=i, representation="tool-call", original_tokens=orig_tok,
                    encoded_tokens=orig_tok))
                continue
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
        # The digest is NOT inserted as its own system message: strict chat
        # templates (Qwen3-family and others) raise "System message must be at
        # the beginning" for any system message past index 0, and the upstream
        # turns that into a hard 500. It is composed into the LEADING system
        # message at the end of assembly instead — after the byte-stable
        # DICT/style injections, so the stable prefix stays cacheable and the
        # per-turn digest is the only part that changes.
        digest_base = digest_alias = ""
        if digest_mode and digest_lines:
            digest_base = "HIST (compact digest of earlier turns; ask to expand any "\
                          "[code:...] ref): " + " | ".join(digest_lines)
            digest_alias = self._alias_pass(digest_base, session)

        # ---- stable injections (system head) --------------------------
        # The upstream API is stateless: definitions must ride along on EVERY
        # request whose encoded content uses codes.
        #
        # Bug (found scanning for more agent-manager blockers, 2026-08-21): this used to
        # inject bootstrap_for(session.used_codes) -- the GROW-ONLY, EVER-ACCUMULATING
        # list of every code a session has used across its whole lifetime -- instead of
        # `used` (already computed two lines below: just the codes this message's own
        # content actually references). The "byte-stable prefix, provider caching
        # amortizes it" reasoning is real for a provider with actual prompt-prefix
        # caching (Anthropic's cache_control, the one real user of this), but this
        # engine's `provider` argument (see Engine.encode) was never threaded down here
        # to gate on it -- the same discount applied unconditionally, including to a
        # plain Ollama upstream with no such mechanism, where every byte of an "amortized"
        # glossary is fully re-sent and fully counted on every single call regardless.
        # Confirmed live: a long-lived worker session had accumulated 116 used_codes: a
        # single-message request whose own alias substitution genuinely won (64->22
        # tokens) still reverted to the full original 64 tokens, because the glossary
        # for all 116 historical codes swamped the eff_overhead comparison even after the
        # 90% cache_discount. Injecting only `used` (a handful of codes, sized to what
        # THIS message actually needs) fixes the decision at its root for every provider,
        # not just non-caching ones -- a smaller glossary is a smaller cost no matter who
        # is paying it, so this is strictly better even where real prefix caching exists.
        inject_parts: list[str] = []
        if cfg.inject_bootstrap:
            from .dictionary import find_codes
            est = self.dict.established_codes()
            scan_texts = [self._text(m) for m in out]
            if digest_alias:
// ... [truncated for review: this function continues for 155 more line(s) not shown]
```

Problem:
This function is a monolithic orchestrator that combines six distinct responsibilities: OFF mode early exit, tiny request optimization, history folding (with tool_calls, digest_mode, alias folding), new message compression, digest aggregation and insertion, and stable code injection with provider-aware fixes. The nested conditionals and side-effecting state updates force readers to trace 6–7 control flows through a single function, violating the Single Responsibility Principle and making unit testing, debugging, and incremental modification extremely difficult.

Solution:
Extract six logical pieces into separate functions: _encode_initial_checks for early exits and entity observation; _fold_history for folding logic; _compress_messages for new message compression; _build_digest for digest aggregation and insertion; _inject_stable_codes for session-specific stable code injection. Each function should accept only the necessary inputs and return only the necessary outputs, with no side effects except for mutating the report or session state in a controlled way.

Benefits:
Decomposing this function improves readability by isolating concerns, enables independent unit testing for each responsibility, and simplifies debugging and incremental modification. Reviewers can focus on one logical unit at a time, reducing cognitive load and minimizing the risk of introducing regressions during changes.

VERDICT CONSTRAINT (mandatory for this task):
Genuine
