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

### AC-7 · Silent JSON-parse failure in Ollama retry path
Strength: Strong
Files: core/tokenfold/adapters/ollama_native.py
Snippet:
```
                    r2 = await client.post(upstream, json=retry, headers=headers)
                    try:
                        obj = r2.json()
                    except Exception:
                        pass
            if isinstance(obj.get("response"), str):
                raw_txt = obj["response"]
```

Problem:
In the retry branch of the Ollama native adapter, after `r2 = await client.post(upstream, json=retry, headers=headers)` the code wraps `obj = r2.json()` in a bare `try/except Exception: pass`. If the upstream returns a non-JSON body (HTML error page, truncated stream, plain-text message), the parse exception is caught and discarded with no log line, no re-raise, and no metric. Execution then falls through to `if isinstance(obj.get("response"), str):` where `obj` still holds whatever value it had before the retry (or is unbound on first pass), so the caller receives a `JSONResponse` built from stale or undefined data while the real parse failure is completely invisible to operators.

Solution:
Replace the bare `except Exception: pass` with an `except Exception as exc:` clause that calls `logging.getLogger(__name__).error("Failed to parse Ollama retry response as JSON (status=%s, upstream=%s): %s", r2.status_code, upstream, exc, exc_info=True)` and then `raise` the exception so the outer `except Exception:` handler (already present at the end of the shown block) can return a proper error response to the client. Add `import logging` at the top of the module if not already present. No new dependency is introduced; `logging` is the Python stdlib primitive this project uses for Python-side diagnostics.

Benefits:
Operators gain a single, greppable log line with the upstream URL, HTTP status code, the exception message, and a full traceback (`exc_info=True`) the moment a retry response is malformed, turning an invisible silent failure into a diagnosable event. Because the exception is re-raised, the existing outer handler returns a well-formed error to the caller instead of serving stale or undefined data, eliminating the risk of a downstream consumer receiving a `JSONResponse` whose `obj` was never actually populated by the retry call.

### AC-8 · Silent exception swallow in terse-compress step hides persistent pipeline failure
Strength: Strong
Files: core/tokenfold/core/encoder.py
Snippet:
```
            try:
                skel, regs = protected.extract(ln)
                ln = protected.restore(phrases.compress(skel), regs).strip()
            except Exception:
                pass
            if len(ln) > 240:               # word-boundary truncation
                cut = ln.rfind(" ", 0, 237)
```

Problem:
In the digest-entry loop, the terse-compress step wraps `protected.extract(ln)` and `phrases.compress(skel)` in a bare `except Exception: pass`. If either call raises—due to a malformed input shape, a regex backtracking blowup, or a bug introduced in `protected` or `phrases`—the exception is discarded with no log, no counter, and no trace. The code then falls through to the 240-char truncation and dedup logic using the *uncompressed* line, so the pipeline still "works," but the compression is silently skipped for every subsequent line. Because the project has no metrics or telemetry system, the only way to discover that the compression step is broken for all inputs would be to manually inspect `session.digest_entries` and notice they are longer than expected, then trace back to this block. A one-off transient error is tolerable; a persistent one is not, and today neither is visible.

Solution:
Replace the bare `except Exception: pass` with a handler that logs a warning via the Python stdlib `logging` module (already the project's sanctioned logging primitive for Python code) before falling back to the original line. Concretely: at module top, ensure `import logging` and `logger = logging.getLogger(__name__)`. In the except clause, call `logger.warning("terse-compress failed for line (len=%d): %s", len(ln), e, exc_info=True)` and then let `ln` retain its pre-`try` value (the original uncompressed line), preserving the existing graceful-degradation behavior. No rethrow is needed because the caller's contract is "store a best-effort compressed line"; the fallback is the intended recovery path. The `exc_info=True` argument ensures the full traceback is captured so a developer can identify whether the failure is in `extract`, `compress`, or `restore` without reproducing the input.

Benefits:
A persistent regression in the compression pipeline (e.g., a bad regex added to `protected.extract`) becomes immediately visible in application logs as a repeating warning with a full traceback, rather than silently degrading every digest entry to its uncompressed form. Operators and developers can distinguish a one-off transient error from a systemic break by the warning frequency, and the `exc_info` traceback points directly at the offending call. Because the fix uses only the stdlib `logging` module already sanctioned for this project, it introduces no new dependency and no new telemetry surface.

### AC-9 · Swallowed exception in best-effort summarisation has no log signal
Strength: Strong
Files: core/tokenfold/core/folding.py
Snippet:
```
                if len(cur.digest_entries) >= len(session.digest_entries):
                    cur.digest_entries = fresh + cur.digest_entries[len(old):]
                    cur.save()
        except Exception:
            pass
        finally:
            self._inflight.discard(key)
```

Problem:
The `except Exception: pass` at the tail of the summarisation `try` block discards every possible failure — an HTTP 500/429 from the model endpoint, a malformed JSON body, a `KeyError` if the response shape changes, a transient DB lock on `cur.save()`, or a schema drift in `r.json()` — with zero trace. Because the enclosing `_work` method returns `None` and the task is fire-and-forget, no caller can inspect a return value or catch the exception. The `finally` clause correctly cleans up `_inflight`, but the only place a human could learn the summarisation is failing is a log line, and there is none. If the model endpoint renames its `"response"` key or the API key rotates, every subsequent call silently enters the `except`, hits `pass`, and the session's digest entries are never summarised again — no alert, no log, no metric.

Solution:
Replace the bare `pass` with a single `logging.warning` call that captures the full traceback via `exc_info=True` and includes the `key` and `session.id` for operator correlation. The `finally` block and `_inflight.discard(key)` remain unchanged. No re-raise is needed because the task is explicitly best-effort and the caller has no contract to fulfil. No metrics, counters, or health signals are added because the project has no metrics system; the stdlib `logging` module (already the project's logging mechanism) is the sole primitive used.

Benefits:
A one-off network blip becomes a single greppable warning line with a full traceback, distinguishable from a persistent schema mismatch that would produce the same warning repeatedly. An operator can correlate the `key` and `session.id` in the log to the specific in-flight task and the affected session. The "don't crash the worker on a transient LLM hiccup" intent is fully preserved — the exception is still caught and the `finally` still cleans up — but the failure is no longer invisible.

### AC-10 · Log the swallowed abstractive-fold failure instead of bare `pass`
Strength: Strong
Files: core/tokenfold/core/folding.py
Snippet:
```
                session.folds.append(Fold(start=start, end=end, summary=summary,
                                          kind="abstractive"))
                session.save()
        except Exception:
            pass                          # extractive fold remains in use
        finally:
            self._inflight.discard(key)
```

Problem:
The `except Exception: pass` block that guards the abstractive-fold HTTP POST (to `/api/generate`) discards every possible failure—network timeout, HTTP 5xx, malformed JSON, expired auth, wrong model name—with no log line, no stderr write, and no other trace. The fallback semantics are correct (the extractive fold stays, `self._inflight.discard(key)` still runs in `finally`), so the code is not a correctness bug. But the operational consequence is that if the LLM endpoint goes down or the configuration drifts, abstractive folding silently and permanently stops producing `kind="abstractive"` folds. An operator has no signal whatsoever; they would only discover the outage by manually diffing session folds over days. The project already has the stdlib `logging` module available for its Python code path, so the fix costs one statement and introduces no new dependency.

Solution:
Replace the bare `pass` with a single `logging.warning` call that captures the exception object (with `exc_info=True` for a full traceback in the log record) and the `key` that identifies the specific fold request, while leaving the `finally` block and the no-rethrow fallback contract untouched. Concretely, bind the exception (`except Exception as exc:`), emit `logging.warning("Abstractive fold failed for key=%r; keeping extractive fold. Error: %s", key, exc, exc_info=True)`, then keep the existing comment and `finally: self._inflight.discard(key)`. No new imports beyond the already-available `logging` module; no metrics, no re-raise, no change to the session or fold state.

Benefits:
Once the warning is in place, any operator tailing the application log (or a log aggregator) immediately sees the failure, the exact exception type and message, the full traceback, and the `key` that correlates the failure to a specific session/fold request. A permanently broken LLM path—expired token, mistyped model name, upstream 500—becomes visible within seconds of the first failed call instead of remaining invisible until someone manually inspects folds. The fix adds zero runtime cost on the success path, introduces no new dependency, and preserves the existing fallback contract exactly.

### AC-11 · Silent deserialization failure in session restore
Strength: Strong
Files: core/tokenfold/core/session.py
Snippet:
```
                self.last_inject_hash = raw.get("last_inject_hash", "")
                self.pending_expansions = raw.get("pending_expansions", [])
                self.turn = raw.get("turn", 0)
            except Exception:
                pass

    def save(self) -> None:
```

Problem:
The `except Exception: pass` block at the tail of the session-restore method (lines 81-82) swallows every possible failure during hydration of `self.entities`, `self.folds`, `self.digested`, `self.turn`, and related fields from the `raw` dict. A corrupt JSON file, a schema drift (a new required kwarg in `Entity(**e)` or `Fold(**f)`), a `TypeError` from a type mismatch, or a `KeyError` from a missing sub-dict all result in the object silently falling back to its `__init__` defaults. The user's accumulated session state is lost with zero diagnostic output—no log line, no stderr write, no re-raise—making the failure invisible to both operators and developers.

Solution:
Add `import logging` at the top of the module (if not already present) and a module-level `logger = logging.getLogger(__name__)`. Replace the bare `pass` inside the `except Exception:` clause with `logger.warning("Failed to restore session state from %s; falling back to defaults.", self.path, exc_info=True)`. This preserves the existing best-effort-restore control flow (the caller still gets a usable object with defaults), while emitting a single warning-level log line carrying the full traceback so the root cause is traceable. No new dependency, no metrics, no re-raise—only the stdlib `logging` module the project already permits for Python code.

Benefits:
Any future deserialization failure—whether from a schema change, a truncated file, or a coding error—produces an immediately visible, searchable log entry with a full stack trace. A developer debugging "why did my session reset?" or an operator noticing vanished folds can identify the exact exception and the offending field within seconds instead of discovering the issue through indirect symptoms. The fallback-to-defaults operational behaviour is unchanged, so no caller contract is broken.

### AC-12 · Swallowed Dictionary Mint Failure Without Logging
Strength: Strong
Files: core/tokenfold/engine.py
Snippet:
```
            self._since_mint[mint_key] = 0
            try:
                enc.dict.mint()
            except Exception:
                pass
        if cache_key and not report.fallback:
            self.metrics.cache_put(cache_key, json.dumps(out, ensure_ascii=False),
```

Problem:  
The `try` block around `enc.dict.mint()` silently catches any exception — including I/O errors, model corruption, or internal state inconsistencies — and does nothing with it. There is no logging, no re-raising, and no metric to alert operators or developers. This makes failures invisible, allowing the system to continue operating with an invalid dictionary state, which may lead to silent data corruption or incorrect encoding. Since the project has no metrics system, no telemetry can be emitted — and since no third-party logging is available, only stdlib `logging` can be used to make failures visible.

Solution:  
Replace the `except Exception: pass` block with a logging statement that records the exception with context: scope, function, and error message. This ensures the failure is visible and diagnosable without altering the function’s contract or introducing metrics.

Benefits:  
Once fixed, operators can detect dictionary mint failures via logs, enabling faster diagnosis and recovery. The system remains functionally unchanged — it still returns `(out, report)` — but failures are no longer silently swallowed, reducing the risk of undetected data corruption or cascading failures.

### AC-13 · Silent Exception Swallowed in Metrics Recording Path
Strength: Strong
Files: core/tokenfold/engine.py
Snippet:
```
            rep.original_tokens = prof.count(decoded)
            rep.encoded_tokens = prof.count(generated)
            self.metrics.record(rep, direction="out", project=scope or self.cfg.scope)
        except Exception:
            pass

    # ------------------------------------------------------------------
```

Problem:
The `try` block in `engine.py` attempts to record token usage metrics for model expansion, but any exception (e.g., missing tokenizer profile, invalid model, malformed input) is silently caught and discarded with `pass`. This creates a critical observability gap: failures in this telemetry path are invisible to operators or developers, potentially leading to undetected billing errors or broken monitoring of token efficiency. Since the project has no metrics system, the only viable fix is to log the exception with context so it’s visible to humans — and since this is a metrics recording block, logging is sufficient to surface the issue.

Solution:
Replace `except Exception: pass` with:

```python
except Exception as e:
    import traceback
    self.logger.error(f"Failed to record metrics for model {model}: {str(e)}")
    # Optionally re-raise if caller needs to handle it — but not required here
    # traceback.print_exc()  # optional for full stack trace if needed
```

Benefits:
Once fixed, any failure in the metrics recording path will be logged with context (model, error message), enabling operators to detect and debug issues like missing token profiles or malformed inputs — preventing silent failures in cost tracking and performance monitoring.

### AC-14 · Silent Exception Swallowing in Tokenizer Profile Instantiation
Strength: Strong
Files: core/tokenfold/tokenizers/registry.py
Snippet:
```
        if name not in out:
            try:
                out[name] = TokenizerProfile(name=name, kind=kind, counter=factory(), exact=exact)
            except Exception:
                pass  # missing asset -> skip profile rather than fail
    return out

```

Problem:
The `except Exception: pass` block in `all_profiles()` silently discards any exception thrown during `TokenizerProfile` instantiation — including missing assets or factory errors — without logging or re-raising. This makes it impossible to know which tokenizers are failing to load, why, or which models are affected. In production, this could silently omit tokenizers from the registry, leading to incorrect or incomplete behavior — e.g., a model expecting a tokenizer that is now missing — with no observability trail.

Solution:
Log the exception with context (profile name, kind, factory) using stdlib logging, and optionally re-raise if the caller needs to handle it. Keep the `pass` if the caller does not need to handle it — but ensure the error is logged so it’s observable.

Benefits:
Operators and developers can now detect missing or failing tokenizers via logs, enabling debugging and preventing silent regressions. The fix uses only stdlib logging — no metrics or telemetry — matching the project’s capabilities and avoiding any new dependencies.

### AC-15 · Silent Cache Decode Failures
Strength: Strong
Files: core/tokenfold/engine.py
Snippet:
```
                    report = EncodeReport(session_id="cache", model=model,
                                          profile="cache", exact_tokenizer=True)
                    return out, report
                except Exception:
                    pass
        out, report = enc.encode(messages, model, session_id=session_id, provider=provider)
        self.metrics.record(report, direction="in", provider=provider,
```

Problem:  
The `try`/`except` block in the cache hit path silently catches and discards any exception raised during `json.loads(encoded)`, including JSON decode errors or corrupted cache data. Since there is no logging or re-raising, failures are completely invisible to the caller — the function returns `(None, None)` or a default report without indicating failure. This could silently corrupt downstream state, especially if the cache holds critical session data or model outputs, and the absence of any telemetry or logging makes root-cause analysis impossible.

Solution:  
Log the exception with context (including `cache_key`, `encoded`, and `session_id`) using `logging.error`, then re-raise the exception to propagate the failure to the caller. This preserves the original intent of the cache hit while surfacing failures for debugging. Since the project has no metrics system, logging to `stderr` via `logging.error` is the only available observability primitive.

Benefits:  
Failures that previously went unnoticed will now surface as logged errors, enabling debugging and preventing silent data corruption. The caller can now handle or recover from cache decode failures appropriately, rather than proceeding with potentially invalid or missing data.

### AC-16 · Silent Failure in Dictionary Minting
Strength: Strong
Files: core/tokenfold/engine.py
Snippet:
```
            self._since_mint[mint_key] = 0
            try:
                enc.dict.mint()
            except Exception:
                pass
        if cache_key and not report.fallback:
            self.metrics.cache_put(cache_key, json.dumps(out, ensure_ascii=False),
```

Problem:
The `try` block around `enc.dict.mint()` silently discards any exception that occurs during dictionary minting — no logging, no re-raising, no telemetry. Since the project has no metrics system, there’s no way to observe how often this silent failure happens. This creates an unobservable operational risk: if dictionary minting fails (e.g., due to invalid state or resource exhaustion), the caller gets no feedback, and the failure goes unnoticed — potentially leading to degraded or incorrect encoding behavior without any alert or trace.

Solution:
Replace the empty `except Exception: pass` block with a logging statement that records the exception with scope context, then re-raise to preserve caller handling. Use `logging.error` with the scope key to aid debugging, and re-raise to maintain original error semantics. This uses only stdlib logging — no metrics or telemetry required.

Benefits:
Once fixed, failures in dictionary minting become observable through logs, enabling operators to detect and respond to issues. The fix preserves caller error handling while adding visibility — eliminating silent failures that could otherwise go unnoticed in production.
