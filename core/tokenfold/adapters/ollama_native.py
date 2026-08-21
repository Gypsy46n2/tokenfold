"""Ollama native-API routes: /api/chat and /api/generate.

Lets clients that speak Ollama's own API (not the OpenAI-compatible /v1
layer) route through TokenFold unchanged — point OLLAMA_URL at the proxy
and everything else keeps working:

  /api/chat      encode messages -> forward -> decode message.content
  /api/generate  encode the prompt (single-message form) -> forward ->
                 decode .response
  /api/*         everything else (ps, tags, show, version, embed, pull...)
                 is transparent passthrough, so health/VRAM probes and
                 model management keep working through the proxy.

Streaming uses Ollama's NDJSON framing (one JSON object per line), not
SSE. Response metadata (eval_count, eval_duration, done_reason, thinking,
tool_calls...) is forwarded untouched — only the text content fields are
decoded, so callers that meter throughput off eval_* stay accurate.

Upstream: config.upstream with a trailing /v1 stripped (the OpenAI routes
and these routes share one upstream setting), overridable per-request via
X-TokenFold-Upstream.
"""

from __future__ import annotations

import json
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from ..engine import Engine

HOP_BY_HOP = {"host", "content-length", "connection", "keep-alive",
              "transfer-encoding", "upgrade", "proxy-authorization", "te",
              "trailers", "accept-encoding"}


def add_ollama_routes(app: FastAPI, eng: Engine, client: httpx.AsyncClient) -> None:

    def _upstream(req: Request) -> str:
        base = (req.headers.get("x-tokenfold-upstream")
                or eng.cfg.upstream).rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        return base

    def _fwd_headers(req: Request) -> dict:
        return {k: v for k, v in req.headers.items()
                if k.lower() not in HOP_BY_HOP
                and not k.lower().startswith("x-tokenfold")}

    def _apply_header_overrides(request: Request) -> None:
        mode_hdr = request.headers.get("x-tokenfold-mode")
        route_hdr = request.headers.get("x-tokenfold-route")
        if mode_hdr:
            eng.cfg.mode = mode_hdr.upper()
            eng.cfg.clamp()
        if route_hdr:
            eng.cfg.route_mode = route_hdr.lower()
            eng.cfg.clamp()

    # -----------------------------------------------------------------
    @app.post("/api/chat")
    async def ollama_chat(request: Request):
        try:
            body = await request.json()
        except Exception:
            raw = await request.body()
            r = await client.post(f"{_upstream(request)}/api/chat",
                                  content=raw, headers=_fwd_headers(request))
            return Response(r.content, r.status_code)

        _apply_header_overrides(request)
        model = body.get("model", "")
        messages = body.get("messages", [])
        sid_hdr = request.headers.get("x-tokenfold-session")
        encoded, report = eng.encode(messages, model, provider="ollama",
                                     session_id=sid_hdr)
        body["messages"] = encoded
        sid = report.session_id

        upstream = f"{_upstream(request)}/api/chat"
        headers = _fwd_headers(request)
        headers["content-type"] = "application/json"

        if body.get("stream"):
            return StreamingResponse(
                _stream_ndjson(upstream, body, headers, model, sid,
                               field=("message", "content")),
                media_type="application/x-ndjson")

        r = await client.post(upstream, json=body, headers=headers)
        try:
            obj = r.json()
            msg = obj.get("message") or {}
            raw0 = msg.get("content")
            # Bug (found scanning the repo for more agent-manager blockers, 2026-08-21):
            # the non-streaming /v1/chat/completions route (proxy.py) auto-detects the
            # model asking to see a folded [ref:...]/[code:...] placeholder and retries
            # once with the real content injected; this route (and /api/generate below)
            # never did, despite sharing the exact same folding mechanism -- only their
            # STREAMING sibling (_stream_ndjson) called expansion_requests at all. A model
            # response asking to expand a fold would ship as-is here instead of getting
            # the automatic re-answer proxy.py gives OpenAI-API callers.
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
            msg = obj.get("message") or {}
            if isinstance(msg.get("content"), str):
                raw_txt = msg["content"]
                msg["content"] = eng.decode(raw_txt, sid)
                eng.record_output(model, raw_txt, msg["content"], sid)
            return JSONResponse(obj, status_code=r.status_code)
        except Exception:
            return Response(r.content, r.status_code)

    # -----------------------------------------------------------------
    @app.post("/api/generate")
    async def ollama_generate(request: Request):
        try:
            body = await request.json()
        except Exception:
            raw = await request.body()
            r = await client.post(f"{_upstream(request)}/api/generate",
                                  content=raw, headers=_fwd_headers(request))
            return Response(r.content, r.status_code)

        _apply_header_overrides(request)
        model = body.get("model", "")
        sid_hdr = request.headers.get("x-tokenfold-session")

        # single-string prompt -> single-message encode, then rebuild.
        # Any system-role message the encoder emits (dictionary preamble)
        # rides in Ollama's dedicated `system` field.
        msgs = []
        if body.get("system"):
            msgs.append({"role": "system", "content": body["system"]})
        msgs.append({"role": "user", "content": body.get("prompt", "")})
        encoded, report = eng.encode(msgs, model, provider="ollama",
                                     session_id=sid_hdr)
        sid = report.session_id
        sys_parts = [m.get("content", "") for m in encoded
                     if m.get("role") == "system" and m.get("content")]
        rest = [m.get("content", "") for m in encoded
                if m.get("role") != "system" and m.get("content")]
        if sys_parts:
            body["system"] = "\n\n".join(sys_parts)
        body["prompt"] = "\n\n".join(rest)

        upstream = f"{_upstream(request)}/api/generate"
        headers = _fwd_headers(request)
        headers["content-type"] = "application/json"

        if body.get("stream"):
            return StreamingResponse(
                _stream_ndjson(upstream, body, headers, model, sid,
                               field=("response",)),
                media_type="application/x-ndjson")

        r = await client.post(upstream, json=body, headers=headers)
        try:
            obj = r.json()
            raw0 = obj.get("response")
            # See ollama_chat's identical fix just above for why this is needed: only
            # the streaming path called expansion_requests before. /api/generate has no
            # message list to append a follow-up turn to (single `prompt` string), so
            # the retry appends the expansion request directly onto the original prompt
            # instead of proxy.py's/ollama_chat's synthetic assistant+user turn pair.
            if isinstance(raw0, str):
                asks = eng.expansion_requests(raw0, sid)
                if asks:
                    supplement = "\n\n".join(
                        f"[expanded {h}]\n{orig}" for h, orig in asks[:3])
                    retry = dict(body)
                    retry["prompt"] = (
                        body.get("prompt", "") + "\n\n" + raw0 +
                        "\n\nExpanded content you requested:\n" + supplement +
                        "\n\nNow answer the previous question.")
                    r2 = await client.post(upstream, json=retry, headers=headers)
                    try:
                        obj = r2.json()
                    except Exception:
                        pass
            if isinstance(obj.get("response"), str):
                raw_txt = obj["response"]
                obj["response"] = eng.decode(raw_txt, sid)
                eng.record_output(model, raw_txt, obj["response"], sid)
            return JSONResponse(obj, status_code=r.status_code)
        except Exception:
            return Response(r.content, r.status_code)

    # -----------------------------------------------------------------
    async def _stream_ndjson(upstream: str, body: dict, headers: dict,
                             model: str, sid: str,
                             field: tuple) -> AsyncIterator[bytes]:
        sd = eng.stream_decoder(sid)
        raw_acc, dec_acc = [], []
        async with client.stream("POST", upstream, json=body,
                                 headers=headers) as r:
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    yield (line + "\n").encode()
                    continue
                # walk to the content field ("response") or ("message","content")
                holder, key = obj, field[-1]
                for part in field[:-1]:
                    holder = holder.get(part) if isinstance(holder, dict) else None
                chunk = holder.get(key) if isinstance(holder, dict) else None
                if isinstance(chunk, str) and chunk:
                    raw_acc.append(chunk)
                    piece = sd.feed(chunk)
                    holder[key] = piece or ""
                    if piece:
                        dec_acc.append(piece)
                if obj.get("done"):
                    tail = sd.flush()
                    if tail and isinstance(holder, dict):
                        dec_acc.append(tail)
                        holder[key] = (holder.get(key) or "") + tail
                    raw, dec = "".join(raw_acc), "".join(dec_acc)
                    eng.record_output(model, raw, dec, sid)
                    eng.expansion_requests(raw, sid)
                yield (json.dumps(obj) + "\n").encode()

    # -----------------------------------------------------------------
    # transparent passthrough for the rest of the native surface
    # (/api/ps, /api/tags, /api/show, /api/version, /api/embed, ...)
    @app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE", "HEAD"])
    async def ollama_passthrough(path: str, request: Request):
        url = f"{_upstream(request)}/api/{path}"
        raw = await request.body()
        r = await client.request(request.method, url,
                                 content=raw or None,
                                 params=dict(request.query_params),
                                 headers=_fwd_headers(request))
        return Response(r.content, r.status_code,
                        media_type=r.headers.get("content-type",
                                                 "application/json"))
