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

### AC-4 · Silent config-load fallback hides parse and I/O failures
Strength: Strong
Files: core/tokenfold/core/config.py
Snippet:
```
        try:
            return Config(**{**asdict(Config()),
                             **json.loads(p.read_text(encoding="utf-8"))}).clamp()
        except Exception:
            pass
    return Config()

```

Problem:
The `load()` function in `core/tokenfold/core/config.py` wraps the JSON parse and `Config` construction in a bare `except Exception: pass`. Any failure—malformed JSON (trailing comma, wrong type), a `PermissionError` on a read-only mount, a `FileNotFoundError` that slips past the `p.exists()` check via TOCTOU, or a `TypeError` from an unexpected key—is swallowed with zero diagnostic output. The subsequent `return Config()` is the same happy-path default returned when the file simply does not exist, so no caller, operator, or log consumer can distinguish "no config file" from "config file was present but unparseable." In a multi-node deployment a partial write or disk fault corrupts the file and every node silently reverts to defaults; the operator sees degraded routing (e.g. `route_mode` falling back to `"human"`) with no log line pointing at the cause.

Solution:
Bind the caught exception (`except Exception as exc`) and emit a single `logging.warning` call inside the `except` block that names the resolved config path `p` and the concrete exception (`exc`). Keep the existing `return Config()` fallback unchanged so the "always return a valid Config" contract is preserved. Add `import logging` at the top of the module if it is not already present. No new dependency, no metrics, no re-raise—just one greppable warning line that tells an operator exactly which file failed and why.

Benefits:
An operator or user who accidentally leaves a stray comma in `config.json` (or whose file is corrupted by a partial write) now sees a single, greppable warning line in the log naming the file path and the specific exception type and message. This turns an invisible, silent fallback into a one-line diagnostic that is immediately actionable, eliminates the ambiguity between "file absent" and "file present but broken," and requires no new dependency or infrastructure—only the stdlib `logging` module the project already uses.

### AC-5 · Silent exception swallow in SSE token-fold proxy loop
Strength: Strong
Files: core/tokenfold/adapters/proxy.py
Snippet:
```
                                    obj["delta"]["text"] = piece
                                    yield ("data: " + json.dumps(obj) + "\n\n").encode()
                                    continue
                            except Exception:
                                pass
                        yield (line + "\n").encode() if line else b"\n"
                    tail = sd.flush()
```

Problem:
Inside the `async for line in r.aiter_lines()` loop, the `try` block performs `json.loads`, a chained `.get()` access, `sd.feed()`, and `json.dumps`. Any failure in that chain (malformed JSON from the upstream LLM, a missing key if the payload shape shifts, an internal error in `sd.feed`) is caught by `except Exception: pass`, which discards the exception object entirely. Control then falls through to `yield (line + "\n").encode()`, emitting the raw `data:`-prefixed line to the downstream SSE consumer instead of the token-folded content. The operator receives no log line, no traceback, and no indication that the folding step failed, making the resulting stream corruption (duplicate or un-folded tokens, broken SSE framing) effectively undiagnosable in production.

Solution:
Import `logging` at module top (stdlib, already permitted by project capabilities) and create `logger = logging.getLogger(__name__)`. Replace the bare `pass` in the `except` clause with `logger.error("tokenfold proxy: failed to process SSE line %r: %s", line[:120], exc, exc_info=True)`. This captures the offending line (truncated to avoid log bloat), the exception type and message, and the full traceback. The existing fall-through `yield` is preserved so the stream does not stall, but the operator now has a concrete, greppable log entry to diagnose the root cause. No new dependency is introduced; `logging` is part of the Python standard library.

Benefits:
Every swallowed exception now produces a single, structured log line with the offending payload fragment and a full traceback, turning an invisible data-corruption path into a diagnosable one. Operators can grep for the logger name to find all folding failures in a given window, correlate them with upstream LLM responses, and confirm whether the fall-through raw-line yield is causing downstream parsing issues. The fix is minimal (one import, one logger line, one `except` body change) and introduces no new runtime dependency or behavioral change to the happy path.

### AC-6 · Silent dictionary reset hides data loss and root cause
Strength: Strong
Files: core/tokenfold/core/dictionary.py
Snippet:
```
                self.aliases = {c: Alias(**a) for c, a in raw.get("aliases", {}).items()}
                self.generations = [Generation(**g) for g in raw.get("generations", [])]
                self.nursery = raw.get("nursery", {})
            except Exception:
                # corrupt dictionary must never block traffic
                self.aliases, self.generations, self.nursery = {}, [], {}

```

Problem:
In `_load()`, the `except Exception:` branch resets `self.aliases`, `self.generations`, and `self.nursery` to empty containers with no log, no warning, and no stderr write. The comment "corrupt dictionary must never block traffic" correctly states the intent, but the operator has zero visibility that every learned alias, generation, and nursery entry was discarded. A corrupt file could indicate a partial `save()` (disk full, kill -9 mid-write), a filesystem error, a serialisation bug, or manual tampering; because nothing is recorded, none of these root causes are diagnosable, and the next `save()` overwrites the evidence.

Solution:
Add `import logging` and `import traceback` at the top of the module (or reuse an existing module-level `logger` if one is already defined). Inside the `except Exception:` block, before the reset assignment, emit a single `logging.getLogger(__name__).warning(...)` call that includes the file path (`self.path`), the exception type and message, and `traceback.format_exc()` so the full stack is captured. The reset-to-empty behaviour and the "never block traffic" contract remain unchanged; the only addition is the one log statement.

Benefits:
An operator grepping service logs will immediately see a single, identifiable line naming the dictionary file, the exception, and the full traceback, making it possible to diagnose the corruption source (disk, serialisation, tampering) and recover the lost vocabulary from backup. The silent data-loss window is closed without introducing any new dependency, any metrics system, or any change to the recovery semantics.
