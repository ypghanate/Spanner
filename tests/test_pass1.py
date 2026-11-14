"""Pass 1: prompt rendering, heuristic fallback, Claude call shape, Adaption fallback, logging."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from types import SimpleNamespace

import anthropic
import httpx
import httpx2
import pytest

from backend import config
from backend.brain import pass1, pass1_adaption
from backend.contracts import SceneObservation, SituationReport, Transcript
from backend.mocks import MockStore, fixture_json
from backend.session import Session
from tests.conftest import set_env
from tests.helpers import run


def obs(n=1) -> SceneObservation:
    return SceneObservation.model_validate(fixture_json(f"scene_observation_{n}"))


def report(**over) -> SituationReport:
    return SituationReport.model_validate({**fixture_json("situation_report_question"), **over})


def inp(text=None, phase="guiding", step=True, observations=None) -> pass1.Pass1Input:
    return pass1.Pass1Input(
        phase=phase,
        observations=observations if observations is not None else [obs(1), obs(2)],
        transcript=None if text is None else Transcript(text=text, language="en", duration_s=2.0),
        current_step=pass1.StepBrief(
            index=2, instruction="Slide the pad onto the braking surface.",
            expected_visual_state="Pad flat on the rim.", common_mistakes=["Pad touching the tyre"]) if step else None,
        previous_summary="The user is loosening the bolt.",
    )


# --- prompt -----------------------------------------------------------------

def test_system_prompt_loads_and_covers_every_intent():
    p = pass1.load_system_prompt()
    for intent in ("start", "question", "step_done", "need_help", "stop", "none"):
        assert intent in p
    for field in SituationReport.model_fields:
        assert field in p, field


def test_render_user_snapshot():
    assert pass1.render_user(inp("Okay, I loosened it. Done.")) == "\n".join([
        "PHASE: guiding",
        "CURRENT STEP: 2. Slide the pad onto the braking surface.",
        "  Done when: Pad flat on the rim.",
        "  Common mistakes: Pad touching the tyre",
        "PREVIOUS SUMMARY: The user is loosening the bolt.",
        "OBSERVATIONS (oldest first):",
        "  [1] frame fixture-00001 | object: bicycle wheel with rim brake | state: wheel installed, brake pad sits "
        "off-centre and touches the rim on one side | parts: rim, brake_pad, brake_arm, tyre, spoke | anomalies: "
        "brake pad rubbing the rim | hands visible: no | tools: none | confidence: 0.82",
        "  [2] frame fixture-00002 | object: bicycle wheel with rim brake | state: hex key inserted in the brake pad "
        "bolt, bolt looks loosened | parts: rim, brake_pad, brake_arm, allen_bolt, tyre | anomalies: none | "
        "hands visible: yes | tools: hex_key | confidence: 0.78",
        'USER SAID: "Okay, I loosened it. Done." (language en, 2.0s)',
    ])


def test_render_user_handles_empty_inputs():
    text = pass1.render_user(inp(None, phase="identifying", step=False, observations=[]))
    assert "CURRENT STEP: none" in text and "  none" in text and "USER SAID: (nothing)" in text
    assert pass1.render_user(inp("   ")).endswith("USER SAID: (nothing)")


def test_render_is_deterministic():
    a, b = inp("hello"), inp("hello")
    assert pass1.render_user(a) == pass1.render_user(b)


def test_build_input_from_session():
    s = Session(id="s", phase="identifying")
    for n in (1, 2, 3, 1):
        s.observations.append(obs(n))
    s.last_report = report(summary="before")
    from backend.contracts import BrainResponse, RepairPlan
    s.apply(BrainResponse(phase="guiding", plan=RepairPlan.model_validate(fixture_json("repair_plan")),
                          current_step=2, step_status="in_progress"))
    built = pass1.build_input(s, Transcript(text="hi", language="en", duration_s=1))
    assert built.phase == "guiding" and len(built.observations) == 3
    assert built.current_step.index == 2 and built.previous_summary == "before"


# --- heuristic fallback -----------------------------------------------------

@pytest.mark.parametrize("text,phase,intent", [
    (None, "guiding", "none"),
    ("   ", "guiding", "none"),
    ("okay I'm done with that", "guiding", "step_done"),
    ("ready for the next one", "guiding", "step_done"),
    ("I'm stuck, the pad won't move", "guiding", "need_help"),
    ("stop, forget it", "guiding", "stop"),
    ("start over please", "guiding", "stop"),
    ("my brake keeps rubbing, can you fix it", "identifying", "start"),
    ("which key do I need", "guiding", "question"),
    ("my brake keeps rubbing, can you fix it", "guiding", "question"),
])
def test_fallback_intent_rules(text, phase, intent):
    r = pass1.fallback_report(inp(text, phase=phase))
    assert r.user_intent == intent
    assert r.confidence == 0.3


def test_fallback_uses_newest_observation_and_utterance():
    r = pass1.fallback_report(inp("which key do I need"))
    assert "hex key" in r.camera_sees and r.user_utterance == "which key do I need"
    assert r.suspected_fault is None                      # newest observation (2) has no anomalies
    r2 = pass1.fallback_report(inp(None, observations=[obs(1)]))
    assert r2.suspected_fault == "brake pad rubbing the rim" and r2.user_utterance is None


def test_fallback_without_observations():
    r = pass1.fallback_report(inp("hi", observations=[]))
    assert r.camera_sees == "No camera observation yet."


# --- claude call ------------------------------------------------------------

def parse_client(result=None, exc=None):
    async def parse(**kwargs):
        parse.kwargs = kwargs
        if exc:
            raise exc
        return result
    return SimpleNamespace(messages=SimpleNamespace(parse=parse)), parse


def ok_message(parsed, stop="end_turn"):
    return SimpleNamespace(stop_reason=stop, parsed_output=parsed)


def test_call_claude_request_shape(monkeypatch):
    client, parse = parse_client(ok_message(report()))
    monkeypatch.setattr(pass1, "_get_client", lambda: client)
    out = run(pass1.call_claude(inp("which key")))
    assert out == report()
    kw = parse.kwargs
    assert kw["model"] == "claude-haiku-5-5"
    assert kw["output_format"] is SituationReport
    assert kw["thinking"] == {"type": "disabled"}
    assert kw["output_config"] == {"effort": "low"}
    assert kw["timeout"] == 10.0 and kw["max_tokens"] >= 1024
    assert kw["system"] == pass1.load_system_prompt()
    assert kw["messages"] == [{"role": "user", "content": pass1.render_user(inp("which key"))}]
    for banned in ("temperature", "top_p", "top_k"):
        assert banned not in kw, "Haiku 5.5 rejects sampling parameters"
    assert kw["messages"][-1]["role"] == "user", "no assistant prefill"


@pytest.mark.parametrize("msg", [ok_message(None), ok_message(report(), stop="refusal"),
                                 ok_message(report(), stop="max_tokens")])
def test_call_claude_unusable_responses_raise(monkeypatch, msg):
    client, _ = parse_client(msg)
    monkeypatch.setattr(pass1, "_get_client", lambda: client)
    with pytest.raises(pass1.Pass1Error):
        run(pass1.call_claude(inp("x")))


def _status_error(cls, code):
    req = httpx2.Request("POST", "http://x")
    return cls("boom", response=httpx2.Response(code, request=req), body=None)


@pytest.mark.parametrize("exc,needle", [
    (anthropic.APITimeoutError(request=httpx2.Request("POST", "http://x")), "timed out"),
    (anthropic.APIConnectionError(request=httpx2.Request("POST", "http://x")), "reach"),
    (_status_error(anthropic.RateLimitError, 429), "rate limited"),
    (_status_error(anthropic.InternalServerError, 500), "HTTP 500"),
    (ValueError("schema mismatch"), "unusable"),
])
def test_call_claude_maps_errors(monkeypatch, exc, needle):
    client, _ = parse_client(exc=exc)
    monkeypatch.setattr(pass1, "_get_client", lambda: client)
    with pytest.raises(pass1.Pass1Error, match=needle):
        run(pass1.call_claude(inp("x")))


def test_run_claude_falls_back_to_heuristic(monkeypatch):
    client, _ = parse_client(exc=anthropic.APITimeoutError(request=httpx2.Request("POST", "http://x")))
    monkeypatch.setattr(pass1, "_get_client", lambda: client)
    r = run(pass1.run_claude(inp("which key")))
    assert r.confidence == 0.3 and r.user_intent == "question"


# --- dispatcher: logging and backends --------------------------------------

def stub_claude(monkeypatch, result=None, exc=None):
    calls = []

    async def fake(i):
        calls.append(i)
        if exc:
            raise exc
        return result
    monkeypatch.setattr(pass1, "call_claude", fake)
    return calls


def jsonl_rows():
    path = config.get_settings().training_data_dir / "raw" / "pass1_calls.jsonl"
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_run_logs_one_row_to_store_and_jsonl(monkeypatch):
    stub_claude(monkeypatch, report())
    store = MockStore()
    out = run(pass1.run(inp("which key"), "sess1", store=store))
    assert out == report()
    (row,) = store.read("pass1_calls")
    assert row["session_id"] == "sess1" and row["backend"] == "claude" and row["valid_json"] is True
    assert set(row) == {"session_id", "ts", "backend", "input_json", "output_json", "latency_ms", "valid_json"}
    assert pass1.Pass1Input.model_validate_json(row["input_json"]) == inp("which key")
    assert SituationReport.model_validate_json(row["output_json"]) == report()
    assert len(jsonl_rows()) == 1 and jsonl_rows()[0]["backend"] == "claude"


def test_run_uses_heuristic_and_marks_it_invalid_when_claude_fails(monkeypatch):
    stub_claude(monkeypatch, exc=pass1.Pass1Error("down"))
    store = MockStore()
    out = run(pass1.run(inp("I'm done"), "s", store=store))
    assert out.user_intent == "step_done"
    (row,) = store.read("pass1_calls")
    assert row["backend"] == "heuristic" and row["valid_json"] is False


def test_run_survives_a_broken_store(monkeypatch):
    stub_claude(monkeypatch, report())

    class Broken:
        def write(self, *a):
            raise RuntimeError("databricks down")
    assert run(pass1.run(inp("x"), "s", store=Broken())) == report()
    assert len(jsonl_rows()) == 1


def adaption_env(monkeypatch, timeout=0.8):
    s = set_env(monkeypatch, PASS1_BACKEND="adaption", PASS1_ADAPTION_MODEL_ID="spanner-p1",
                TOGETHER_API_KEY="tok", ADAPTION_BASE_URL="https://llm.test/v1")
    fast = dataclasses.replace(s, adaption_timeout_s=timeout)
    monkeypatch.setattr(pass1, "get_settings", lambda: fast)
    monkeypatch.setattr(pass1_adaption, "get_settings", lambda: fast)
    return fast


def adaption_client(monkeypatch, handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(pass1_adaption, "_client", client)
    return client


def chat_reply(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def test_adaption_good_json_is_used_and_request_is_well_formed(monkeypatch):
    adaption_env(monkeypatch)
    seen = {}

    def handler(request):
        seen["url"], seen["auth"] = str(request.url), request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return chat_reply(report(summary="from adaption").model_dump_json())
    adaption_client(monkeypatch, handler)
    claude_calls = stub_claude(monkeypatch, report(summary="from claude"))
    store = MockStore()
    out = run(pass1.run(inp("which key"), "s", store=store))
    assert out.summary == "from adaption" and claude_calls == []
    assert store.read("pass1_calls")[0]["backend"] == "adaption"
    assert seen["url"] == "https://llm.test/v1/chat/completions" and seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert body["model"] == "spanner-p1" and body["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": pass1.load_system_prompt()}
    assert body["messages"][1] == {"role": "user", "content": pass1.render_user(inp("which key"))}


def test_adaption_accepts_code_fenced_json(monkeypatch):
    adaption_env(monkeypatch)
    adaption_client(monkeypatch, lambda r: chat_reply("```json\n" + report().model_dump_json() + "\n```"))
    stub_claude(monkeypatch, report(summary="claude"))
    assert run(pass1.run(inp("x"), "s", store=MockStore())) == report()


@pytest.mark.parametrize("name,handler", [
    ("bad json", lambda r: chat_reply("not json at all")),
    ("wrong shape", lambda r: chat_reply('{"summary": "x"}')),
    ("http 500", lambda r: httpx.Response(500, text="boom")),
    ("odd body", lambda r: httpx.Response(200, json={"nope": 1})),
])
def test_adaption_failures_fall_back_to_claude(monkeypatch, name, handler):
    adaption_env(monkeypatch)
    adaption_client(monkeypatch, handler)
    stub_claude(monkeypatch, report(summary="from claude"))
    store = MockStore()
    out = run(pass1.run(inp("x"), "s", store=store))
    assert out.summary == "from claude"
    row = store.read("pass1_calls")[0]
    assert row["backend"] == "adaption_fallback" and row["valid_json"] is True


def test_slow_adaption_times_out_and_falls_back(monkeypatch):
    adaption_env(monkeypatch, timeout=0.05)

    async def slow(request):
        await asyncio.sleep(1.0)
        return chat_reply(report().model_dump_json())
    adaption_client(monkeypatch, slow)
    stub_claude(monkeypatch, report(summary="from claude"))
    store = MockStore()
    out = run(pass1.run(inp("x"), "s", store=store))
    assert out.summary == "from claude"
    assert store.read("pass1_calls")[0]["backend"] == "adaption_fallback"


def test_adaption_without_credentials_falls_back(monkeypatch):
    set_env(monkeypatch, PASS1_BACKEND="adaption")
    stub_claude(monkeypatch, report(summary="from claude"))
    store = MockStore()
    assert run(pass1.run(inp("x"), "s", store=store)).summary == "from claude"
    assert store.read("pass1_calls")[0]["backend"] == "adaption_fallback"


def test_adaption_and_claude_both_down_gives_heuristic(monkeypatch):
    adaption_env(monkeypatch)
    adaption_client(monkeypatch, lambda r: httpx.Response(500))
    stub_claude(monkeypatch, exc=pass1.Pass1Error("down"))
    store = MockStore()
    out = run(pass1.run(inp("I'm done"), "s", store=store))
    assert out.user_intent == "step_done"
    assert store.read("pass1_calls")[0]["backend"] == "heuristic"


def test_run_defaults_to_the_mock_store(monkeypatch):
    from backend import mocks
    stub_claude(monkeypatch, report())
    run(pass1.run(inp("x"), "s"))
    assert len(mocks.get_stages().store.read("pass1_calls")) == 1


# --- live (needs ANTHROPIC_API_KEY) -----------------------------------------

@pytest.mark.live
def test_haiku_returns_a_valid_report_quickly():
    if not os.getenv("ANTHROPIC_API_KEY"):
        pytest.skip("needs ANTHROPIC_API_KEY")
    import time
    t0 = time.perf_counter()
    r = run(pass1.call_claude(inp("Okay, I've loosened the bolt. Done.")))
    elapsed = time.perf_counter() - t0
    assert r.user_intent == "step_done"
    assert elapsed < 3.0, f"Pass 1 took {elapsed:.1f}s (target ~1s)"


# --- Pass 1 vs the step tracker: separate Adaption models --------------------

def endpoint_counting(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return chat_reply(report(summary="from adaption").model_dump_json())
    adaption_client(monkeypatch, handler)
    return calls


def test_the_step_trackers_model_id_alone_never_reaches_the_pass1_endpoint(monkeypatch):
    """ADAPTION_MODEL_ID belongs to the step tracker. Setting it must not make Pass 1 call that model."""
    set_env(monkeypatch, PASS1_BACKEND="adaption", ADAPTION_MODEL_ID="step-tracker-ft",
            TOGETHER_API_KEY="tok", ADAPTION_BASE_URL="https://llm.test/v1")
    calls = endpoint_counting(monkeypatch)
    stub_claude(monkeypatch, report(summary="from claude"))
    store = MockStore()
    out = run(pass1.run(inp("x"), "s", store=store))
    assert out.summary == "from claude" and calls == []
    assert store.read("pass1_calls")[0]["backend"] == "adaption_fallback"


def test_a_pass1_model_id_equal_to_the_step_trackers_is_refused(monkeypatch):
    set_env(monkeypatch, PASS1_BACKEND="adaption", ADAPTION_MODEL_ID="same-id", PASS1_ADAPTION_MODEL_ID="same-id",
            TOGETHER_API_KEY="tok", ADAPTION_BASE_URL="https://llm.test/v1")
    calls = endpoint_counting(monkeypatch)
    with pytest.raises(pass1_adaption.AdaptionError, match="step tracker"):
        run(pass1_adaption.run_adaption(inp("x")))
    stub_claude(monkeypatch, report(summary="from claude"))
    assert run(pass1.run(inp("x"), "s", store=MockStore())).summary == "from claude"
    assert calls == []


def test_both_models_can_be_configured_at_once(monkeypatch):
    set_env(monkeypatch, PASS1_BACKEND="adaption", ADAPTION_MODEL_ID="step-tracker-ft",
            PASS1_ADAPTION_MODEL_ID="pass1-ft", TOGETHER_API_KEY="tok", ADAPTION_BASE_URL="https://llm.test/v1")
    calls = endpoint_counting(monkeypatch)
    out = run(pass1.run(inp("x"), "s", store=MockStore()))
    assert out.summary == "from adaption"
    assert json.loads(calls[0].content)["model"] == "pass1-ft"


def test_adaption_api_key_takes_precedence_over_the_together_key(monkeypatch):
    set_env(monkeypatch, PASS1_BACKEND="adaption", PASS1_ADAPTION_MODEL_ID="pass1-ft", TOGETHER_API_KEY="together",
            ADAPTION_API_KEY="other-provider", ADAPTION_BASE_URL="https://llm.test/v1")
    calls = endpoint_counting(monkeypatch)
    run(pass1.run(inp("x"), "s", store=MockStore()))
    assert calls[0].headers["authorization"] == "Bearer other-provider"


def test_adaption_works_with_only_adaption_api_key_set(monkeypatch):
    set_env(monkeypatch, PASS1_BACKEND="adaption", PASS1_ADAPTION_MODEL_ID="pass1-ft",
            ADAPTION_API_KEY="only-this", ADAPTION_BASE_URL="https://llm.test/v1")
    calls = endpoint_counting(monkeypatch)
    assert run(pass1.run(inp("x"), "s", store=MockStore())).summary == "from adaption"
    assert calls[0].headers["authorization"] == "Bearer only-this"


def test_eval_only_counts_adaption_as_configured_with_the_pass1_model_id(monkeypatch):
    from training import eval as ev
    set_env(monkeypatch, ADAPTION_MODEL_ID="step-tracker-ft", TOGETHER_API_KEY="tok")
    assert ev.adaption_configured() is False
    set_env(monkeypatch, PASS1_ADAPTION_MODEL_ID="pass1-ft")
    assert ev.adaption_configured() is True
