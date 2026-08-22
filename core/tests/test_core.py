"""Core safety + pipeline tests."""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# isolate all persistence into a temp TOKENFOLD_HOME before imports
_tmp = tempfile.mkdtemp(prefix="tokenfold-test-")
os.environ["TOKENFOLD_HOME"] = _tmp

from tokenfold.core import phrases, protected, verifier  # noqa: E402
from tokenfold.core.config import Config  # noqa: E402
from tokenfold.core.decoder import Decoder, StreamDecoder  # noqa: E402
from tokenfold.core.dictionary import Dictionary  # noqa: E402
from tokenfold.core.encoder import Encoder  # noqa: E402
from tokenfold.core.session import Entity, Session  # noqa: E402


# ---------------------------------------------------------------- protected
PROTECT_CASES = [
    "Modify C:\\AgentManager\\server\\main.py but don't change API_ROUTE=/api/v2",
    "run `pip install -r requirements.txt` then open https://example.com/x?a=1&b=2",
    "```python\ndef f(x):\n    return x + 1  # don't touch\n```",
    'config is {"key": "value", "nested": {"a": [1, 2, 3]}} ok',
    "hash 5d41402abc4b2a76b9719d911017c592 uuid 123e4567-e89b-12d3-a456-426614174000",
    "email me at user@example.com about ~/projects/tokenfold/main.py",
    "version v2.31.4 costs $1,499 i.e. 1,499 USD at 99.9% uptime in 250 ms",
    "the snake_case_name and camelCaseName and pkg.mod.func() references",
    "date 2026-08-20T14:30:00Z and file paths D:/data/x.csv and \\\\srv\\share\\f.txt",
    "key sk-abc123def456ghi789jkl012 should never be altered",
]


def test_protected_roundtrip_exact():
    for t in PROTECT_CASES:
        assert protected.verify_roundtrip(t), t


def test_protected_lifts_path_and_const():
    t = "Modify C:\\AgentManager\\server\\main.py but don't change API_ROUTE=/api/v2"
    skel, regs = protected.extract(t)
    assert "main.py" not in skel
    assert "API_ROUTE" not in skel
    assert "don't" in skel                      # negation stays in skeleton
    texts = [r.text for r in regs]
    assert any("main.py" in x for x in texts)
    # nested regions allowed; the restored whole must be byte-exact
    assert protected.restore(skel, regs) == t


def test_placeholder_collision_survives():
    t = "literal ⟦0⟧ in input plus a path C:\\x\\y.txt"
    skel, regs = protected.extract(t)
    assert protected.restore(skel, regs) == t


# ---------------------------------------------------------------- phrases
def test_phrase_compression_drops_filler_keeps_negation():
    t = "Please make sure that you do not delete the backup files."
    out = phrases.compress(t)
    assert "please" not in out.lower()
    assert "do not" in out.lower()
    assert phrases.negation_signature(t) == phrases.negation_signature(out)


def test_phrase_compression_never_flips_do_not_delete():
    t = "do not delete anything; delete only temp files older than 30 days"
    out = phrases.compress(t)
    v = verifier.verify(t, out)
    assert v.ok, v.failures


def test_at_least_rewrite_keeps_number_semantics():
    t = "compare at least 3 approaches and at most 10 options"
    out = phrases.compress(t)
    assert ">=3" in out and "<=10" in out
    assert verifier.verify(t, out).ok


# ---------------------------------------------------------------- verifier
def test_verifier_catches_dropped_negation():
    v = verifier.verify("do not delete the files", "delete the files")
    assert not v.ok


def test_verifier_catches_number_change():
    v = verifier.verify("keep less than 10 items", "keep less than 100 items")
    assert not v.ok


def test_verifier_catches_modality_flip():
    v = verifier.verify("this step is optional", "this step is required")
    assert not v.ok


def test_verifier_passes_equivalent():
    v = verifier.verify(
        "Please analyze the file and do not modify unrelated code, keep 3 tests",
        "analyze file; do not modify unrelated code; keep 3 tests")
    assert v.ok


# ---------------------------------------------------------------- dictionary
def test_alias_net_savings_math():
    d = Dictionary(scope="t1", path=Path(_tmp) / "t1.json")
    for _ in range(5):
        d.observe("do not modify files unrelated to the current task", 9)
    promos = d.promotable()
    assert promos, "frequent long phrase should be promotable"
    gen = d.mint()
    assert gen is not None and gen.version == 1
    assert gen.bootstrap.startswith("DICT v1")
    # unprofitable: short phrase seen once
    d2 = Dictionary(scope="t2", path=Path(_tmp) / "t2.json")
    d2.observe("run the tests", 3)
    assert not d2.promotable()
    assert d2.mint() is None


def test_bootstrap_byte_stable():
    d = Dictionary(scope="t3", path=Path(_tmp) / "t3.json")
    for _ in range(4):
        d.observe("preserve all public application interfaces exactly", 8)
    d.mint()
    b1 = d.bootstrap_block()
    d_reload = Dictionary(scope="t3", path=Path(_tmp) / "t3.json")
    assert d_reload.bootstrap_block() == b1


# ---------------------------------------------------------------- decoder
def test_decoder_expands_known_keeps_unknown():
    d = Dictionary(scope="t4", path=Path(_tmp) / "t4.json")
    d.add_manual("K1", "preserve existing behavior")
    dec = Decoder(d)
    assert dec.decode("apply fix; K1; K99 stays") == \
        "apply fix; preserve existing behavior; K99 stays"


def test_stream_decoder_handles_split_codes():
    d = Dictionary(scope="t5", path=Path(_tmp) / "t5.json")
    d.add_manual("K12", "run the full test suite")
    dec = Decoder(d)
    sd = StreamDecoder(dec)
    out = sd.feed("first K") + sd.feed("12 then done. ") + sd.flush()
    assert out == "first run the full test suite then done. "


def test_encoder_expand_codes_does_not_corrupt_longer_codes_sharing_a_prefix():
    # Regression: Encoder._expand_codes used to str.replace() codes one at a time in
    # dict-insertion order, so "K4" (inserted early) matched as a literal substring
    # inside "K40", "K400", etc. (inserted later) and mangled them -- "K400" decoded to
    # K4's expansion plus a stray "00", not K400's own expansion. A single word-bounded
    # regex pass (matching the pattern Decoder.decode() already uses correctly) makes
    # this substring collision impossible regardless of how many codes exist or what
    # order they were minted in.
    expansions = {"K4": "run the full test suite after changes",
                  "K40": "SENTINEL FORTY", "K400": "SENTINEL FOUR HUNDRED"}
    session = Session("t-expand-collision")
    out = Encoder._expand_codes("K400 K40 K4", expansions, session)
    assert out == "SENTINEL FOUR HUNDRED SENTINEL FORTY run the full test suite after changes"


def test_encoder_expand_codes_entity_codes_do_not_corrupt_longer_entity_codes():
    # Same collision class, the E-code (session entity) side: a separate naive
    # str.replace() loop for entities had the identical bug -- "E1" is a literal
    # substring of "E10". Not yet hit in practice (a session accumulates entities
    # slower than dictionary codes) but structurally the same landmine.
    session = Session("t-expand-entity-collision")
    for i in range(1, 12):
        session.entities[f"E{i}"] = Entity(code=f"E{i}", name=f"SENTINEL {i}")
    out = Encoder._expand_codes("E10 E1", {}, session)
    assert out == "SENTINEL 10 SENTINEL 1"


# ---------------------------------------------------------------- encoder
def _cfg(**kw) -> Config:
    base = dict(mode="BALANCED", inject_bootstrap=False,
                inject_terse_style=False, fold_history=False)
    base.update(kw)
    return Config(**base)


def test_encoder_compresses_and_verifies():
    enc = Encoder(_cfg())
    msgs = [{"role": "user", "content":
             "Please analyze the following Python program, identify any bugs, "
             "fix all of them, make sure you preserve the existing "
             "functionality, and explain each of the changes you made."}]
    out, rep = enc.encode(msgs, "gpt-4o")
    assert rep.encoded_tokens < rep.original_tokens
    assert not rep.fallback
    assert rep.messages[0].representation in ("terse", "terse+alias")


def test_encoder_never_ships_longer_message():
    enc = Encoder(_cfg())
    msgs = [{"role": "user", "content": "fix bug"}]
    out, rep = enc.encode(msgs, "gpt-4o")
    assert rep.encoded_tokens <= rep.original_tokens


def test_encoder_passthrough_on_off():
    enc = Encoder(_cfg(mode="OFF"))
    msgs = [{"role": "user", "content": "Please analyze this and that."}]
    out, rep = enc.encode(msgs, "gpt-4o")
    assert out == msgs


def test_encoder_preserves_protected_bytes():
    enc = Encoder(_cfg())
    content = ("Please modify C:\\AgentManager\\server\\main.py carefully but "
               "don't change API_ROUTE=/api/v2 and keep sk-abc123def456ghi789jkl "
               "working exactly")
    out, rep = enc.encode([{"role": "user", "content": content}], "gpt-4o")
    enc_text = out[-1]["content"]
    assert "C:\\AgentManager\\server\\main.py" in enc_text
    assert "API_ROUTE=/api/v2" in enc_text
    assert "sk-abc123def456ghi789jkl" in enc_text
    assert "don't" in enc_text


def test_encoder_digests_old_turns():
    cfg = _cfg(fold_history=True, fold_after_turns=1)
    enc = Encoder(cfg)
    long_answer = "\n".join(f"line {i} of a long explanation about caching"
                            for i in range(40)) + "\nerror: cache miss was the bug"
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Explain caching in our Agent Manager system."},
        {"role": "assistant", "content": long_answer},
        {"role": "user", "content": "Now apply that fix please."},
    ]
    out, rep = enc.encode(msgs, "gpt-4o")
    assert rep.encoded_tokens < rep.original_tokens
    joined = "\n".join(m["content"] for m in out)
    # old turns absorbed into one digest; the key line survives in it
    assert "error: cache miss was the bug" in joined
    assert len(out) < len(msgs)


def test_encoder_learns_repeated_prose_sentences_not_just_semicolon_clauses():
    # Regression: the nursery-learning step used to split only on ";", per this
    # project's own documented FoldLang target style (docs/foldlang.md: "Clauses
    # separated by ;"). A caller whose real repeated boilerplate is normal
    # grammatical prose -- complete sentences, no semicolons -- got zero nursery
    # observations no matter how many times that exact text repeated. Confirmed live
    # (agent-manager integration): 40 identical repeats of a real instructional
    # sentence never moved the nursery count. Sentence-boundary splitting alongside
    # the semicolon split fixes this without changing FoldLang-style input at all.
    d = Dictionary(scope="t-prose-learn", path=Path(_tmp) / "t-prose-learn.json")
    enc = Encoder(_cfg(inject_bootstrap=False), dictionary=d)
    sentence = "A deterministic scanner flagged a possible issue in this project."
    msg = [{"role": "user", "content": sentence + " Please look at it."}]
    for _ in range(3):
        enc.encode(msg, "gpt-4o")
    key = sentence.strip().lower()
    assert key in d.nursery
    assert d.nursery[key]["count"] >= 3


def test_encoder_fail_soft(monkeypatch):
    enc = Encoder(_cfg())
    monkeypatch.setattr("tokenfold.core.encoder.protected.extract",
                        lambda t: (_ for _ in ()).throw(RuntimeError("boom")))
    msgs = [{"role": "user", "content": "Please analyze everything now."}]
    out, rep = enc.encode(msgs, "gpt-4o")
    assert out[0]["content"] == msgs[0]["content"]


# ---------------------------------------------------------------- session
def test_session_entity_learning():
    s = Session("test-ent")
    s.observe_entities("The Agent Manager talks to the Agent Manager daily.")
    assert any(e.name == "Agent Manager" for e in s.entities.values())


def test_blob_dedup():
    s = Session("test-blob")
    blob = "x" * 500
    h1, seen1 = s.note_blob(blob, "output")
    h2, seen2 = s.note_blob(blob, "output")
    assert h1 == h2 and not seen1 and seen2


# ------------------------------------------------- total-cost alias decision
def test_short_message_prefers_terse_over_bootstrap():
    """One short message: bootstrap can't amortize -> terse-only, no codes."""
    from tokenfold.core.seed import seed
    enc = Encoder(_cfg(inject_bootstrap=True))
    seed(enc.dict)
    msgs = [{"role": "user", "content":
             "Please fix the bug and make sure you preserve the existing "
             "functionality of the parser module."}]
    out, rep = enc.encode(msgs, "gpt-4o")
    joined = " ".join(m["content"] for m in out)
    assert rep.dictionary_overhead == 0
    assert "DICT v" not in joined
    assert rep.encoded_tokens <= rep.original_tokens


def test_message_representation_reflects_what_actually_shipped_not_just_what_was_tried():
    # Regression (found scanning for more agent-manager blockers, 2026-08-21):
    # MessageReport.representation was stamped from the per-message candidate search
    # alone and never corrected when the total-cost gate (alias body + glossary >
    # terse-only body) or the absolute-invariant gate rejected that candidate and
    # shipped something cheaper instead. A caller reading report.messages (or anything
    # aggregating by representation, like Metrics.summary()'s by_representation
    # breakdown) would see "terse+alias" for a request that, in reality, shipped
    # uncompressed -- reporting what was ATTEMPTED, not what was SENT. Confirmed live:
    # exactly this shape, a single-message request whose per-message alias candidate
    # won internally (64->22 tokens) but whose overall report.encoded_tokens equaled
    # report.original_tokens because the glossary injection cost wasn't worth it.
    from tokenfold.core.seed import seed
    enc = Encoder(_cfg(inject_bootstrap=True))
    seed(enc.dict)
    msgs = [{"role": "user", "content":
             "Please fix the bug and make sure you preserve the existing "
             "functionality of the parser module."}]
    out, rep = enc.encode(msgs, "gpt-4o")
    # This fixture is the same one test_short_message_prefers_terse_over_bootstrap uses
    # to confirm the bootstrap gate rejects the alias candidate (dictionary_overhead ==
    # 0, no "DICT v" block shipped) -- the new assertion here is that the per-message
    # label agrees with that outcome instead of independently claiming "terse+alias".
    assert rep.dictionary_overhead == 0
    for m in rep.messages:
        if m.representation == "terse+alias":
            assert False, (
                "a message reported as terse+alias must correspond to a request that "
                "actually shipped a smaller/aliased body -- this one shipped uncompressed"
            )


def test_boilerplate_session_adopts_aliases():
    """Repeated boilerplate across turns: aliases + bootstrap win on total."""
    from tokenfold.core.seed import seed
    enc = Encoder(_cfg(inject_bootstrap=True, fold_history=True,
                       fold_after_turns=2))
    seed(enc.dict)
    boiler = ("Do not break existing functionality, run the tests before "
              "finishing, preserve all public APIs, update the documentation "
              "whenever behavior changes, and never modify files that are "
              "unrelated to the current task. ")
    hist = [{"role": "system", "content": "You are a coding agent."}]
    rep = None
    for i in range(4):
        hist.append({"role": "user", "content": boiler + f"Do task number {i}."})
        hist.append({"role": "assistant", "content":
                     "Done. " + "Filler sentence about the work. " * 30})
        out, rep = enc.encode(hist, "gpt-4o")
    joined = " ".join(m["content"] for m in out)
    assert "P1" in joined                      # bundle adopted
    assert "DICT v" in joined                  # definitions ride along
    assert rep.saved > rep.original_tokens * 0.5   # >50% by turn 4


def test_ollama_provider_never_ships_more_raw_tokens_than_original():
    # Regression (found live against real agent-manager traffic, 2026-08-21, layered on
    # top of the decision/invariant-agreement fix from earlier the same day): once
    # decision and invariant agree on the SAME (discounted) eff_overhead, the alias path
    # reliably ships -- correct for a provider with real prompt-prefix caching, where the
    # discount reflects genuine amortized cost. Ollama has no such mechanism: it re-reads
    # the full injection block byte-for-byte on every call. Confirmed live: a real
    # multi-code observability_fix prompt shipped 1663 raw tokens against a 1574-token
    # original once turn>=2 unlocked the discount -- an 89-token real increase, not a
    # one-time investment, since nothing is ever actually amortized for this provider.
    # provider="ollama" must keep the decision honest at every turn, matching what a
    # provider with zero memory of past calls actually experiences.
    from tokenfold.core.seed import seed
    enc = Encoder(_cfg(inject_bootstrap=True))
    seed(enc.dict)
    boiler = ("Do not break existing functionality, run the tests before "
              "finishing, preserve all public APIs, update the documentation "
              "whenever behavior changes, and never modify files that are "
              "unrelated to the current task. ")
    for i in range(4):
        msgs = [{"role": "user", "content": boiler + f"Do task number {i}."}]
        out, rep = enc.encode(msgs, "gpt-4o", session_id="ollama-turns", provider="ollama")
        assert rep.encoded_tokens + rep.dictionary_overhead <= rep.original_tokens, \
            f"turn {i}: shipped more raw tokens than the original for provider=ollama"

    # The same repeated boilerplate through a provider that DOES have real caching must
    # still reach the documented amortized win (unchanged behavior, not weakened by the
    # gate above) -- same content, same turn count, only the declared provider differs.
    enc2 = Encoder(_cfg(inject_bootstrap=True))
    seed(enc2.dict)
    last_rep = None
    for i in range(4):
        msgs = [{"role": "user", "content": boiler + f"Do task number {i}."}]
        _, last_rep = enc2.encode(msgs, "gpt-4o", session_id="anthropic-turns", provider="anthropic")
    # saved (face value) can still be negative here -- the DICT block's real byte cost
    # doesn't vanish just because a caching provider would bill/process it cheaply.
    # saved_effective is the number that's supposed to turn positive once the discount
    # is legitimately in play, which is exactly what this asserts stayed true.
    assert last_rep.saved_effective > 0


# ------------------------------------------------- expansion + tool folding
def test_pending_expansion_ships_original_once():
    from tokenfold.core.session import Session, content_hash
    enc = Encoder(_cfg(fold_history=True, fold_after_turns=1, mode="MAX"))
    old_text = "The magic constant is 7741 and it matters a great deal. " * 8
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": old_text},
        {"role": "assistant", "content": "Noted. " * 40},
        {"role": "user", "content": "What was that constant again?"},
    ]
    out1, rep1 = enc.encode(msgs, "gpt-4o")
    sid = rep1.session_id
    # digest mode: the old user turn is absent verbatim
    assert not any(m.get("content") == old_text for m in out1)
    # model asks for it -> pending; next encode ships the original once
    s = Session(sid)
    s.pending_expansions.append(content_hash(old_text))
    s.save()
    out2, rep2 = enc.encode(msgs, "gpt-4o")
    assert any(m.get("content") == old_text for m in out2)
    assert content_hash(old_text) not in Session(sid).pending_expansions


def test_old_tool_results_become_refs():
    enc = Encoder(_cfg(fold_history=True, fold_after_turns=1, mode="MAX"))
    big_result = "row %d | value\n" * 1 + "x" * 500
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "Search the logs for errors please."},
        {"role": "assistant", "content": None,
         "tool_calls": [{"id": "t1", "type": "function",
                         "function": {"name": "grep", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "t1", "content": big_result},
        {"role": "assistant", "content": "Found nothing important."},
        {"role": "user", "content": "OK, now check warnings."},
    ]
    out, rep = enc.encode(msgs, "gpt-4o")
    tool_msgs = [m for m in out if m.get("role") == "tool"]
    assert len(tool_msgs) == 1                       # message kept (API pairing)
    assert tool_msgs[0]["tool_call_id"] == "t1"
    assert "[ref:" in tool_msgs[0]["content"]        # content replaced by ref
    assert rep.encoded_tokens < rep.original_tokens


def test_output_metrics_split(tmp_path):
    from tokenfold.core.metrics import Metrics
    from tokenfold.core.encoder import EncodeReport
    m = Metrics(path=str(tmp_path / "m.sqlite3"))
    rep_in = EncodeReport(session_id="s", model="x", profile="p",
                          exact_tokenizer=True)
    rep_in.original_tokens, rep_in.encoded_tokens = 100, 60
    m.record(rep_in, direction="in")
    rep_out = EncodeReport(session_id="s", model="x", profile="p",
                           exact_tokenizer=True)
    rep_out.original_tokens, rep_out.encoded_tokens = 80, 50   # expanded vs generated
    m.record(rep_out, direction="out")
    s = m.summary()
    assert s["orig"] == 100 and s["saved"] == 40                # input side only
    assert s["output"]["generated"] == 50 and s["output"]["saved"] == 30


# ------------------------------------------------- imported packs activate
def test_import_json_mints_a_generation_and_activates_codes():
    """An imported pack is useless until its codes are generation-frozen:
    only established codes substitute or ship in a DICT block. Importing
    must therefore mint; re-importing the same pack must not mint again."""
    import json as _json
    d = Dictionary(scope="import-activation-test")
    from tokenfold.core.seed import seed
    seed(d)
    pack = _json.dumps({"aliases": {
        "K201": {"code": "K201",
                 "expansion": "always write the changelog entry last",
                 "surface_forms": [r"always write the changelog entry last"]}}})
    d.import_json(pack)
    assert "K201" in d.established_codes(), "imported code must be established"
    assert any(c == "K201" for _, c in d.substitutable()), "and substitutable"
    gens = len(d.generations)
    d.import_json(pack)
    assert len(d.generations) == gens, "re-import of the same pack must not mint"


def test_code_expansion_has_no_substring_collisions():
    """K1 must never expand inside K101. The verifier judges candidates on
    their decoded form, so a collision there silently killed every encoding
    that used a 3+ digit code — and the Decoder must agree byte-for-byte."""
    import json as _json
    d = Dictionary(scope="collision-test")
    from tokenfold.core.seed import seed
    seed(d)
    d.import_json(_json.dumps({"aliases": {
        "K101": {"code": "K101", "expansion": "the hundred-and-first rule",
                 "surface_forms": [r"the hundred-and-first rule"]}}}))
    text = "apply K101 and K1 now"
    expected = "apply the hundred-and-first rule and analyze the program now"
    assert Decoder(d).decode(text) == expected
    enc = Encoder(Config(), d)
    got = enc._expand_codes(text, d.expansions(), Session("collision-s"))
    assert got == expected, got


def test_established_alias_pack_is_not_vetoed_at_face_value():
    """The candidate decision prices the DICT block at cache-effective cost;
    the never-larger invariant must judge at the SAME cost, or every alias
    encoding that wins the decision is vetoed right after (codes then never
    ship at all). Wire may exceed the original only when a DICT block first
    ships — the priced-in investment."""
    import json as _json
    d = Dictionary(scope="invariant-consistency-test")
    from tokenfold.core.seed import seed
    seed(d)
    sentence = ("Before changing architecture, agent behavior, data stores, "
                "tool wiring, scheduler flows, or linked-machine behavior, "
                "read the system map first and update it in the same commit.")
    d.import_json(_json.dumps({"aliases": {
        "K150": {"code": "K150", "expansion": sentence,
                 "surface_forms": [__import__("re").escape(sentence)]}}}))
    enc = Encoder(Config(), d)
    sysm = {"role": "system", "content": "Standing rule: " + sentence}
    history = []
    shipped_codes = False
    for turn in range(1, 5):
        history.append({"role": "user",
                        "content": f"Turn {turn}: proceed with the refactor step."})
        msgs = [sysm] + history
        out, rep = enc.encode(msgs, "gpt-4o", session_id="invariant-s")
        joined = "\n".join(m.get("content", "") for m in out
                           if isinstance(m.get("content"), str))
        if "K150" in joined:
            shipped_codes = True
        history.append({"role": "assistant", "content": f"Step {turn} done."})
    assert shipped_codes, "an established alias never reached the wire"


def test_digest_never_becomes_a_second_system_message():
    """Strict chat templates (Qwen3-family) raise 'System message must be at
    the beginning' for any system message past index 0, and the upstream turns
    that into a hard 500. The HIST digest must therefore ride INSIDE the
    leading system message, never as its own."""
    enc = Encoder(Config(mode="MAX"))
    sysm = {"role": "system", "content": "You are a terse assistant."}
    history = []
    hist_seen = hist_in_head = False
    for turn in range(1, 6):
        history.append({"role": "user",
                        "content": f"Turn {turn}: continue the analysis of the retry helper, "
                                   "compare the exponential and linear strategies in detail, "
                                   "and keep the backoff capped at sixty seconds please. "
                                   "Also restate the acceptance criteria we agreed on so the "
                                   "reviewer can follow the whole decision without scrolling."})
        msgs = [sysm] + history
        out, rep = enc.encode(msgs, "gpt-4o", session_id="strict-template-s")
        roles = [m.get("role") for m in out]
        assert roles.count("system") <= 1, f"turn {turn}: {roles}"
        if "system" in roles:
            assert roles[0] == "system", f"turn {turn}: system not first: {roles}"
        for i, m in enumerate(out):
            if isinstance(m.get("content"), str) and "HIST" in m["content"]:
                hist_seen = True
                if i == 0 and m.get("role") == "system":
                    hist_in_head = True
        history.append({"role": "assistant", "content": f"Step {turn} done, cap held."})
    # On turns where folding won, the digest must exist AND live in the system
    # head. (A turn where the invariant ships the original untouched is fine.)
    assert hist_seen, "no turn ever produced a digest - folding never engaged"
    assert hist_in_head, "digest appeared outside the leading system message"


def test_assistant_tool_calls_message_is_never_folded_away():
    """An old assistant message carrying tool_calls must survive folding — its
    paired role:'tool' message keeps a tool_call_id the API requires be
    introduced by a preceding assistant tool_calls, or the upstream 400s."""
    enc = Encoder(Config(mode="MAX"))
    msgs = [
        {"role": "system", "content": "You are a worker."},
        {"role": "assistant", "content": "calling a tool",
         "tool_calls": [{"id": "call_1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "file contents " * 40},
        {"role": "user", "content": "now summarize what you found in the file"},
    ]
    out, _rep = enc.encode(msgs, "gpt-4o", session_id="toolcalls-s")
    tool_ids = {m.get("tool_call_id") for m in out if m.get("role") == "tool"}
    call_ids = {tc["id"] for m in out if m.get("role") == "assistant"
                for tc in (m.get("tool_calls") or [])}
    for tid in tool_ids:
        assert tid in call_ids, f"tool message {tid} lost its assistant tool_calls pairing"


def test_bundle_pattern_respects_code_boundaries():
    """K3;K4;K5;K6;K78 must NOT collapse to P18 — the trailing K6 must not
    swallow K78 (this path ships unverified)."""
    from tokenfold.core.seed import bundle_patterns
    import re as _re
    text = "rules: K3; K4; K5; K6; K78 extra"
    for pat, pcode in bundle_patterns():
        text = _re.sub(pat, pcode, text)
    assert "K78" in text, f"K78 was corrupted: {text!r}"


def test_stream_decoder_does_not_corrupt_words_ending_in_a_code():
    """Feeding 'Board OK1' then ' done' must not turn OK1 into an expansion."""
    from tokenfold.core.decoder import Decoder, StreamDecoder
    d = Dictionary(scope="stream-boundary-test")
    from tokenfold.core.seed import seed
    seed(d)
    sd = StreamDecoder(Decoder(d))
    got = sd.feed("Board OK1") + sd.feed(" done") + sd.flush()
    assert got == "Board OK1 done", repr(got)


def test_stream_decoder_does_not_stall_on_a_lone_bracket():
    """A '[' that never closes must be flushed, not held until stream end."""
    from tokenfold.core.decoder import Decoder, StreamDecoder
    d = Dictionary(scope="stream-bracket-test")
    from tokenfold.core.seed import seed
    seed(d)
    sd = StreamDecoder(Decoder(d))
    out = sd.feed("use [ to open an array literal in the language, ")
    out += sd.feed("then keep typing normally for a while afterward")
    # the bracket-held text must have been emitted mid-stream, not stranded
    assert "to open an array literal" in (out + sd.flush())


def test_entity_alias_pass_respects_word_boundaries():
    """_alias_pass must not turn 'Agent Managers' into 'E1s' (unverified path)."""
    from tokenfold.core.session import Session
    enc = Encoder(Config())
    s = Session("entity-boundary-s")
    s.observe_entities("Agent Manager is the system. Agent Manager rocks.")
    # force an entity to exist with a code
    if s.entity_pairs():
        out = enc._alias_pass("The Agent Managers shipped it", s)
        assert "s" in out  # plural survives
        assert "E" not in out or "Managers" in out or "E1s" not in out, out


# ------------------------------------------------------------- engine cache
def test_engine_cache_does_not_leak_across_sessions(tmp_path, monkeypatch):
    # Regression: Engine.encode()'s response cache key never included session_id, so
    # two DIFFERENT sessions sending byte-identical single-message content got the
    # same cache entry -- and a cache hit skips self.encoder.encode() entirely, which
    # is the only place session.save() (turn/used_codes) happens. Confirmed live
    # (agent-manager integration): repeated identical prompts across separate calls
    # all returned session_id="cache", silently starving whichever session hit the
    # cache of the turn increments the session-continuity mechanism depends on.
    from tokenfold.engine import Engine
    monkeypatch.setenv("TOKENFOLD_HOME", str(tmp_path))
    eng = Engine(_cfg(inject_bootstrap=False))
    msg = [{"role": "user", "content": "Please analyze this and that carefully."}]

    eng.encode(msg, "gpt-4o", session_id="session-a")
    out_a2, rep_a2 = eng.encode(msg, "gpt-4o", session_id="session-a")
    assert rep_a2.session_id == "cache"          # same session, second call: cache hit

    out_b, rep_b = eng.encode(msg, "gpt-4o", session_id="session-b")
    assert rep_b.session_id != "cache"            # different session: must NOT hit
    assert rep_b.session_id == "session-b"

    from tokenfold.core.session import Session
    assert Session("session-b").turn >= 1         # session-b's own state actually advanced


# ------------------------------------------------------------ per-scope dictionaries
def test_engine_scopes_have_independent_dictionaries(tmp_path, monkeypatch):
    # Grimmethy, 2026-08-21: "Each job type could have it's own folded dictionary" --
    # a worker session bouncing between task types (observability_review,
    # arch_discovery, etc.) was diluting every one of those templates' own real
    # repetition into one shared dictionary. A phrase observed under one scope must
    # never leak into another scope's nursery/dictionary.
    from tokenfold.engine import Engine
    from tokenfold.core.dictionary import Dictionary
    monkeypatch.setenv("TOKENFOLD_HOME", str(tmp_path))
    eng = Engine(_cfg(inject_bootstrap=False, seed_dictionary=False))

    phrase_msg = [{"role": "user", "content":
                   "A deterministic scanner flagged a possible issue in this project."}]
    for _ in range(3):
        eng.encode(phrase_msg, "gpt-4o", session_id="s1", scope="observability_review")

    obs_dict = Dictionary(scope="observability_review")
    arch_dict = Dictionary(scope="arch_discovery")
    key = "a deterministic scanner flagged a possible issue in this project."
    assert key in obs_dict.nursery
    assert key not in arch_dict.nursery
    # the engine's own default (unscoped) dictionary must be untouched too
    assert key not in eng.dict.nursery


def test_engine_scope_cache_does_not_leak_across_scopes(tmp_path, monkeypatch):
    # Same class of bug as the session-cache-leak fix above, the scope axis instead of
    # the session axis: two DIFFERENT scopes sending byte-identical content must never
    # share a cache entry, since a hit skips scope-specific dictionary state updates
    # (mint counters, nursery observations) the same way it would skip session state.
    from tokenfold.engine import Engine
    monkeypatch.setenv("TOKENFOLD_HOME", str(tmp_path))
    eng = Engine(_cfg(inject_bootstrap=False))
    msg = [{"role": "user", "content": "Please analyze this and that carefully."}]

    eng.encode(msg, "gpt-4o", session_id="s1", scope="observability_review")
    out2, rep2 = eng.encode(msg, "gpt-4o", session_id="s1", scope="observability_review")
    assert rep2.session_id == "cache"              # same scope, second call: cache hit

    out3, rep3 = eng.encode(msg, "gpt-4o", session_id="s1", scope="arch_discovery")
    assert rep3.session_id != "cache"               # different scope: must NOT hit


def test_engine_decode_uses_the_matching_scope_dictionary():
    # K1/K2/... mean completely different things in different scopes -- decoding
    # against the wrong scope's dictionary must not silently expand a code as the
    # WRONG phrase (or leave it unexpanded because that scope never defined it).
    from tokenfold.engine import Engine
    eng = Engine(_cfg(inject_bootstrap=False, seed_dictionary=False))
    obs_enc = eng._encoder_for("observability_review")
    obs_enc.dict.add_manual("K1", "an observability-scoped phrase")
    arch_enc = eng._encoder_for("arch_discovery")
    arch_enc.dict.add_manual("K1", "an arch-discovery-scoped phrase")

    assert eng.decode("see K1 for context", "sess", scope="observability_review") \
        == "see an observability-scoped phrase for context"
    assert eng.decode("see K1 for context", "sess", scope="arch_discovery") \
        == "see an arch-discovery-scoped phrase for context"


# ------------------------------------------------- fallback accounting
def test_small_request_passes_through_without_counting_as_fallback():
    """Tiny requests can never clear min-savings; they must skip the pipeline
    (no latency burned) and must NOT be stamped fallback — live metrics showed
    a cluster of 5–35 token requests inflating fallback_pct with what is
    really correct 'nothing to do here' behavior."""
    enc = Encoder(Config(mode="MAX"))
    out, rep = enc.encode([{"role": "user", "content": "hi there"}],
                          "gpt-4o", session_id="small-req-s")
    assert out == [{"role": "user", "content": "hi there"}]
    assert not rep.fallback and rep.fallback_reason == ""
    assert [m.representation for m in rep.messages] == ["small-passthrough"]


def test_encoder_error_fallback_carries_a_reason():
    """A real error must be distinguishable from an economics revert."""
    enc = Encoder(Config())
    orig = enc._encode
    def boom(*a, **k):
        raise RuntimeError("synthetic")
    enc._encode = boom
    try:
        out, rep = enc.encode([{"role": "user", "content": "x " * 100}], "gpt-4o")
    finally:
        enc._encode = orig
    assert rep.fallback and rep.fallback_reason == "error:RuntimeError"
    assert out[0]["content"] == "x " * 100


def test_metrics_summary_splits_fallback_and_reports_effective_savings(tmp_path):
    from tokenfold.core.metrics import Metrics
    from tokenfold.core.encoder import EncodeReport
    m = Metrics(path=str(tmp_path / "m.sqlite3"))
    ok = EncodeReport(session_id="s", model="x", profile="p", exact_tokenizer=True)
    ok.original_tokens, ok.encoded_tokens = 100, 60
    ok.dictionary_overhead, ok.effective_overhead = 30, 3
    m.record(ok)
    err = EncodeReport(session_id="s", model="x", profile="p", exact_tokenizer=True,
                       fallback=True, fallback_reason="error:ValueError")
    err.original_tokens = err.encoded_tokens = 50
    m.record(err)
    rev = EncodeReport(session_id="s", model="x", profile="p", exact_tokenizer=True,
                       fallback=True, fallback_reason="not-worth-it")
    rev.original_tokens = rev.encoded_tokens = 50
    m.record(rev)
    s = m.summary()
    assert s["n"] == 3
    assert round(s["error_pct"]) == 33 and round(s["reverted_pct"]) == 33
    assert s["saved"] == 100 - 60 - 30            # face value
    assert s["saved_effective"] == 100 - 60 - 3   # prefix-cache aware
    assert s["overhead_effective"] == 3
