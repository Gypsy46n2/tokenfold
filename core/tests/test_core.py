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
from tokenfold.core.session import Session  # noqa: E402


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
