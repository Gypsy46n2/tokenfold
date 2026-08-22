"""OpenAI-compatible reverse proxy.

Point any client at http://localhost:9339/v1 instead of its normal base URL.
Upstream selection, per-request:

  1. X-TokenFold-Upstream header (full base URL)
  2. config.upstream (default: local Ollama http://localhost:11434/v1)

Other headers:
  X-TokenFold-Mode:  FAST | BALANCED | MAX | OFF   (per-request override)
  X-TokenFold-Route: human | agent
Auth headers are passed through untouched; TokenFold never reads or stores
them.

Endpoints: /v1/chat/completions (encode+decode, streaming + non-streaming),
/v1/models (passthrough), /tokenfold/stats, /tokenfold/dashboard, /healthz.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from ..engine import Engine

HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive",
              "transfer-encoding", "upgrade", "proxy-authorization", "te",
              "trailers", "accept-encoding"}


def create_app(engine: Engine | None = None) -> FastAPI:
    app = FastAPI(title="TokenFold", version="0.1.0")
    eng = engine or Engine()
    client = httpx.AsyncClient(timeout=httpx.Timeout(300, connect=15))

    def _upstream(req: Request) -> str:
        return (req.headers.get("x-tokenfold-upstream")
                or eng.cfg.upstream).rstrip("/")

    def _fwd_headers(req: Request) -> dict:
        return {k: v for k, v in req.headers.items()
                if k.lower() not in HOP_BY_HOP
                and not k.lower().startswith("x-tokenfold")}

    # -----------------------------------------------------------------
    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "mode": eng.cfg.mode}

    @app.get("/tokenfold/stats")
    async def stats():
        return JSONResponse(eng.metrics.summary())

    @app.get("/tokenfold/dashboard")
    async def dashboard():
        s = eng.metrics.summary()
        rows_m = "".join(
            f"<tr><td>{m['model']}</td><td>{m['n']}</td><td>{m['saved']}</td>"
            f"<td>{m['avg_pct']}%</td></tr>" for m in s["by_model"])
        rows_r = "".join(
            f"<tr><td>{r['representation']}</td><td>{r['n']}</td>"
            f"<td>{r['saved']}</td></tr>" for r in s["by_representation"])
        html = f"""<!-- tokenfold dashboard -->
<title>TokenFold</title>
<style>body{{font:14px system-ui;margin:2rem;max-width:720px}}
table{{border-collapse:collapse;margin:1rem 0}}td,th{{border:1px solid #8884;
padding:.3rem .7rem;text-align:left}}h1{{font-size:1.3rem}}
.big{{font-size:2rem;font-weight:700}}</style>
<h1>TokenFold — lifetime savings</h1>
<div class=big>{s['saved']:,} tokens saved ({s['reduction_pct']}%)</div>
<p>{s['n']} requests · original {s['orig']:,} → encoded {s['enc']:,}
 (+{s['overhead']:,} dictionary overhead) · avg encode
 {s['avg_latency']:.1f} ms · fallback rate {s['fallback_pct']:.1f}%</p>
<h2>By model</h2><table><tr><th>model</th><th>reqs</th><th>saved</th>
<th>avg %</th></tr>{rows_m}</table>
<h2>By representation</h2><table><tr><th>rep</th><th>reqs</th><th>saved</th>
</tr>{rows_r}</table>"""
        return HTMLResponse(html)

    # -----------------------------------------------------------------
    @app.get("/v1/models")
    async def models(request: Request):
        r = await client.get(f"{_upstream(request)}/models",
                             headers=_fwd_headers(request))
        return Response(r.content, r.status_code,
                        media_type=r.headers.get("content-type", "application/json"))

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
                async with client.stream("POST", upstream, json=body,
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

    def _sse_delta(text: str) -> bytes:
        obj = {"choices": [{"index": 0, "delta": {"content": text},
                            "finish_reason": None}]}
        return ("data: " + json.dumps(obj) + "\n\n").encode()

    add_anthropic_routes(app, eng, client)
    from .ollama_native import add_ollama_routes
    add_ollama_routes(app, eng, client)
    return app


def add_anthropic_routes(app: FastAPI, eng: Engine, client: httpx.AsyncClient) -> None:
    """Anthropic Messages API support: POST /v1/messages.

    Same pipeline; system prompt is a top-level field in this schema.
    Upstream defaults to https://api.anthropic.com/v1 unless overridden by
    X-TokenFold-Upstream. Auth (x-api-key) passes through untouched.
    """

    @app.post("/v1/messages")
    async def anthropic_messages(request: Request):
        try:
            body = await request.json()
        except Exception:
            return Response(status_code=400)
        upstream = (request.headers.get("x-tokenfold-upstream")
                    or "https://api.anthropic.com/v1").rstrip("/")
        headers = {k: v for k, v in request.headers.items()
                   if k.lower() not in HOP_BY_HOP
                   and not k.lower().startswith("x-tokenfold")}

        model = body.get("model", "claude")
        # map to OpenAI-style list for the encoder: system first
        msgs = []
        sys_prompt = body.get("system")
        if isinstance(sys_prompt, str) and sys_prompt:
            msgs.append({"role": "system", "content": sys_prompt})
        for m in body.get("messages", []):
            c = m.get("content")
            if isinstance(c, str):
                msgs.append({"role": m.get("role"), "content": c})
            else:
                msgs.append(m)  # tool blocks etc.: untouched
        # Bug (found scanning the repo for more agent-manager blockers, 2026-08-21): this
        # was the only route (of /v1/chat/completions, /api/chat, /api/generate) that
        # never passed session_id through to encode() -- every request landed on a
        # fresh, one-off, content-derived session no matter what the caller sent in
        # X-TokenFold-Session, so this route could never get the session-continuity
        # benefit at all. Not yet observed live against this route in practice; caught by
        # code inspection, matching the same header every sibling route already reads.
        sid_hdr = request.headers.get("x-tokenfold-session")
        scope_hdr = request.headers.get("x-tokenfold-scope")
        encoded, report = eng.encode(msgs, model, session_id=sid_hdr, scope=scope_hdr)
        new_sys = None
        out_msgs = []
        for m in encoded:
            if m.get("role") == "system":
                new_sys = ((new_sys + "\n") if new_sys else "") + m["content"]
            else:
                out_msgs.append(m)
        if new_sys:
            # cache_control breakpoint: Claude only prefix-caches marked
            # blocks, and our injected system head is byte-stable by design
            body["system"] = [{"type": "text", "text": new_sys,
                               "cache_control": {"type": "ephemeral"}}]
        body["messages"] = [m for m in out_msgs]
        sid = report.session_id

        if body.get("stream"):
            async def gen():
                sd = eng.stream_decoder(sid, scope=scope_hdr)
                async with client.stream("POST", f"{upstream}/messages",
                                         json=body, headers=headers) as r:
                    async for line in r.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                obj = json.loads(line[5:].strip())
                                if obj.get("type") == "content_block_delta" and \
                                        obj.get("delta", {}).get("type") == "text_delta":
                                    piece = sd.feed(obj["delta"]["text"])
                                    if not piece:
                                        continue
                                    obj["delta"]["text"] = piece
                                    yield ("data: " + json.dumps(obj) + "\n\n").encode()
                                    continue
                            except Exception:
                                pass
                        yield (line + "\n").encode() if line else b"\n"
                    tail = sd.flush()
                    if tail:
                        obj = {"type": "content_block_delta", "index": 0,
                               "delta": {"type": "text_delta", "text": tail}}
                        yield ("data: " + json.dumps(obj) + "\n\n").encode()
            return StreamingResponse(gen(), media_type="text/event-stream")

        r = await client.post(f"{upstream}/messages", json=body, headers=headers)
        try:
            obj = r.json()
            for block in obj.get("content", []):
                if block.get("type") == "text":
                    block["text"] = eng.decode(block["text"], sid, scope=scope_hdr)
            return JSONResponse(obj, status_code=r.status_code)
        except Exception:
            return Response(r.content, r.status_code)
