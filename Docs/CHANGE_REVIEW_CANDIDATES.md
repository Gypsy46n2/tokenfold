# Change Review Candidates

### AC-1 · The new `/api/chat` streaming route silently drops the stream-decoder tail (buffered text  (2c91bed ollama_native.py)
Strength: Strong
Source: change_review of 2c91bed "Add native Ollama API adapter (/api/chat, /api/generate, passthrough)"
Files: core/tokenfold/adapters/ollama_native.py

Snippet:
```
diff --git a/core/tokenfold/adapters/ollama_native.py b/core/tokenfold/adapters/ollama_native.py
new file mode 100644
index 0000000..c3c6508
+++ b/core/tokenfold/adapters/ollama_native.py
@@ -0,0 +1,210 @@
+"""Ollama native-API routes: /api/chat and /api/generate.
+
+Lets clients that speak Ollama's own API (not the OpenAI-compatible /v1
+layer) route through TokenFold unchanged — point OLLAMA_URL at the proxy
+and everything else keeps working:
+
+  /api/chat      encode messages -> forward -> decode message.content
+  /api/generate  encode the prompt (single-message form) -> forward ->
+                 decode .response
+  /api/*         everything else (ps, tags, show, version, embed, pull...)
+                 is transparent passthrough, so health/VRAM probes and
+                 model management keep working through the proxy.
+
+Streaming uses Ollama's NDJSON framing (one JSON object per line), not
+SSE. Response metadata (eval_count, eval_duration, done_reason, thinking,
+tool_calls...) is forwarded untouched — only the text content fields are
+decoded, so callers that meter throughput off eval_* stay accurate.
+
+Upstream: config.upstream with a trailing /v1 stripped (the OpenAI routes
+and these routes share one upstream setting), overridable per-request via
+X-TokenFold-Upstream.
...[snippet truncated]
```

Problem: [severity: med; regression shipped in 2c91bed] The new `/api/chat` streaming route silently drops the stream-decoder tail (buffered text from `sd.flush()`) when the terminal NDJSON line carries no `message` field, so the client receives a truncated decoded response and `eng.record_output` logs a shorter-than-true output.  Failure scenario: `POST /api/chat` with `{"model":"llama3","messages":[{"role":"user","content":"hi"}],"stream":true}`. Upstream returns three NDJSON lines: `{"model":"llama3","message":{"role":"assistant","content":"Hel"},"done":false}`, `{"model":"llama3","message":{"role":"assistant","content":"lo"},"done":false}`, `{"model":"llama3","done":true,"total_duration":987654321,"eval_count":2}`. If the incremental stream decoder buffers the final token (e.g. a multi-byte sequence or a token whose boundary is only resolvable at flush), `sd.flush()` returns a non-empty string `tail`. Because the `done` line has no `"message"` key, the walk `holder = holder.get("message")` yields `None`, so `isinstance(holder, dict)` is `False`; the `tail` is never appended to `dec_acc`, never written into any yielded line, and `eng.record_output(model, raw, dec, sid)` records a `dec` that is shorter than the true decoded output by exactly `len(tail)` characters. The client's last content line ("lo") is missing the tail text.
Solution: In the `if obj.get("done"):` block, unconditionally append the tail to `dec_acc` before the `holder` guard (i.e. `if tail: dec_acc.append(tail)`), and only attempt the `holder[key]` mutation when `holder` is a dict. This preserves the record_output / expansion accounting while leaving the done-line JSON unchanged (it has no content field to patch for `/api/chat`).
Benefits: Restores correct behaviour for the scenario above; undoes the regression shipped in 2c91bed.
