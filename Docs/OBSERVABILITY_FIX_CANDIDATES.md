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
