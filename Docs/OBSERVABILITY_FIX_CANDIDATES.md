# Observability Fix Candidates

### AC-1 · Silent swallow of retry-response JSON parse failure in ollama_native adapter
Strength: Strong
Files: core/tokenfold/adapters/ollama_native.py
Snippet:
```
                    r2 = await client.post(upstream, json=retry, headers=headers)
                    try:
                        obj = r2.json()
                    except Exception:
                        pass
            msg = obj.get("message") or {}
            if isinstance(msg.get("content"), str):
```

Problem:
In the retry path (the `if asks:` block), after issuing the second upstream POST, the code attempts `obj = r2.json()` inside a bare `try/except Exception: pass`. If the retry response body is malformed, truncated, or carries a non-JSON content-type, the exception is discarded and execution falls through to `msg = obj.get("message") or {}` using whatever `obj` held from the *first* response. The operator receives no log line, no error status, and no indication that the retry body was unreadable; the returned `JSONResponse` is silently built from stale first-response data. In the edge case where `obj` was never successfully assigned before this point (e.g. the first response also failed parsing and was handled elsewhere), the same `pass` leads to a confusing `NameError` at the `.get("message")` call rather than a clear diagnostic.

Solution:
Replace the bare `except Exception: pass` with an `except Exception as exc` handler that (1) emits `log.warning("ollama_native: retry response JSON parse failed (status=%s); returning error", r2.status_code, exc_info=True)` using the stdlib `logging` module already available in the project, and (2) immediately returns `JSONResponse({"error": "retry response was not valid JSON"}, status_code=r2.status_code or 502)` so the failure is caller-visible and the stale `obj` is never reused. No rethrow is needed because the caller is an HTTP adapter whose contract is to return a `Response`; no metric or counter is added because the project has no metrics system.

Benefits:
An operator debugging a misbehaving upstream now sees a timestamped warning with the HTTP status code and full traceback in the application log, can distinguish "retry body was garbage" from "retry succeeded but content was unexpected," and the HTTP client receives an explicit 502-class error instead of a silently-wrong 200 built from the first response. The `NameError` edge case is eliminated because the function returns before reaching the `obj.get(...)` line. No new dependency is introduced; the fix uses only `logging` (stdlib) and the existing `JSONResponse` return type already present in the file.

### AC-2 · Silent `except Exception` in SSE delta parser drops all diagnostic context
Strength: Strong
Files: core/tokenfold/adapters/proxy.py
Snippet:
```
                        try:
                            obj = json.loads(payload)
                            delta = obj["choices"][0]["delta"].get("content")
                        except Exception:
                            yield (line + "\n\n").encode()
                            continue
                        if delta:
```

Problem:
In the streaming SSE handler, the `try` block that parses each upstream `data:` payload (`json.loads(payload)` followed by the `obj["choices"][0]["delta"].get("content")` chain) is guarded by a bare `except Exception` that does two things: it yields the raw line verbatim to the client and then `continue`s. No log is emitted, no exception type or message is captured, and no caller-visible signal is produced. Because the except clause is a catch-all `Exception`, it silently absorbs `json.JSONDecodeError` (truncated or non-JSON upstream frames), `KeyError` (an upstream error object, a `usage`-only frame, or any non-OpenAI-shaped payload), and `TypeError` (a `None` or list where a dict is expected). In production, a sustained upstream degradation—rate-limit error objects, a proxy in front of the model emitting keepalive comments as `data:` lines, or a transient TCP truncation—produces zero server-side evidence. An operator investigating "why did the client get garbled output?" has no log line to grep, no correlation ID, and no way to distinguish a malformed frame from a valid one. The forwarded raw line may also be non-JSON or a partial frame, which can break the client's SSE/JSON parser and produce truncated or garbled completions rather than a clean, attributable error.

Solution:
Replace the bare `except Exception` with `except Exception as exc`, emit a `logger.warning` call (using the stdlib `logging` module the project already uses) that records the exception type and message, the `model` and `sid` values already in scope, and a truncated copy of the raw line (first ~200 chars) for post-hoc inspection. After logging, `continue` to skip the malformed frame rather than forwarding it to the client, because the downstream decoder (`sd.feed`) expects a clean delta string and the client's SSE parser expects well-formed `data:` JSON; a dropped frame is far less damaging than a corrupted one, and the normal path still terminates with `data: [DONE]`. No metric or counter is added because this project has no metrics system; the stdlib log line is the correct and only available primitive.

Benefits:
Once fixed, every malformed or unexpected upstream SSE frame produces a single, greppable log line carrying the model name, session ID, exception class, exception message, and a bounded raw-line excerpt. An operator can immediately distinguish "upstream sent a rate-limit error object" from "a network hiccup truncated the JSON" from "a non-OpenAI provider sent a different schema," and can correlate the event to a specific session. The client no longer receives a potentially non-JSON or partial frame that could corrupt its SSE parser, so end-user completions fail cleanly (missing a token) rather than garbling. The observability gap that made this class of failure invisible in production is closed using only primitives the project already has.

### AC-3 · Silent JSON-parse failure on retry response in proxy adapter
Strength: Strong
Files: core/tokenfold/adapters/proxy.py
Snippet:
```
                    r2 = await client.post(upstream, json=retry, headers=headers)
                    try:
                        obj = r2.json()
                    except Exception:
                        pass
            for ch in obj.get("choices", []):
                msg = ch.get("message", {})
```

Problem:
Inside the retry branch of the content-expansion path, `r2.json()` is wrapped in `try/except Exception: pass`. If the upstream returns a non-JSON body (an HTML 502 page, a plain-text error, a truncated response), the exception is discarded with no log, no re-raise, and no marker. The variable `obj` silently retains whatever value it held from the initial `r1` parse, and the code then iterates `obj.get("choices", [])` as though the retry had succeeded. In production this means an operator sees a "successful" response that is actually stale data from the first attempt, with zero trace in the logs that the retry round-trip produced an unparseable body. Distinguishing "retry worked" from "retry returned garbage and we fell back" is impossible without adding a log line.

Solution:
Replace the bare `except Exception: pass` with a handler that logs the failure via the stdlib `logging` module (already the project's Python logging primitive) and then lets `obj` retain its prior value as an intentional fallback. Concretely: `except Exception as exc: logging.getLogger(__name__).warning("Retry response from %s was not valid JSON (%s); falling back to initial response", upstream, exc)`. This preserves the existing control flow (the loop over `obj.get("choices", [])` still runs with the prior `obj`) while giving operators a single, greppable log line that names the upstream URL and the parse error, so a wave of 502 HTML pages from the upstream becomes immediately visible in the log stream. No new dependency is introduced; `logging` is stdlib and is the pattern this codebase already uses for Python-side diagnostics.

Benefits:
Operators can now distinguish a healthy retry from a silently-failed one by grepping for the warning message. If the upstream begins returning non-JSON error pages (a common failure mode during deploys or rate-limiting), the log spike is visible within seconds rather than being masked by stale-but-valid-looking responses. The fallback-to-prior-`obj` behaviour is preserved, so no caller contract changes, but the decision is now explicit and auditable rather than an invisible `pass`.
