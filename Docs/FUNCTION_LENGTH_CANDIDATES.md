# Function Length Decomposition Candidates

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
