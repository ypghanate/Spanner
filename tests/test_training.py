"""Dataset builder and eval: slots, validation, caching, splitting, formats, metrics, the PASS/FAIL gate."""
from __future__ import annotations

import asyncio
import json
import xml.etree.ElementTree as ET
from collections import Counter

import pytest

from backend.brain import pass1
from backend.brain.pass1 import Pass1Error, Pass1Input, StepBrief
from backend.contracts import SceneObservation, SituationReport
from backend.mocks import fixture_json
from tests.helpers import run
from training import eval as ev
from training import make_dataset as md


# --- stubs ------------------------------------------------------------------

def obs(n=1) -> SceneObservation:
    return SceneObservation.model_validate(fixture_json(f"scene_observation_{n}"))


class Stubs:
    """Scenario + teacher stand-ins. The transcript starts with the intent word so the teacher can 'read' it."""

    def __init__(self, wrong_for=(), fail_for=(), drop_scenarios=()):
        self.scenario_calls = 0
        self.teacher_calls = 0
        self.wrong_for, self.fail_for, self.drop = set(wrong_for), set(fail_for), set(drop_scenarios)

    async def scenario_fn(self, slots):
        self.scenario_calls += 1
        out = []
        for s in slots:
            if s.id in self.drop:
                continue
            text = None if s.intent == "none" else f"{s.intent} please about the {s.object} {s.id}"
            out.append(md.ScenarioOut(
                slot_id=s.id, observations=[obs(1 + (i % 3)) for i in range(3)], transcript_text=text,
                current_step=StepBrief(index=2, instruction="Do the thing.", expected_visual_state="Done.",
                                       common_mistakes=["Rushing"]) if s.needs_step else None,
                previous_summary="before" if hash(s.id) % 3 else None))
        return out

    async def teacher_fn(self, inp):
        self.teacher_calls += 1
        text = inp.transcript.text if inp.transcript else ""
        if any(w in text for w in self.fail_for):
            raise Pass1Error("teacher down")
        intent = text.split()[0] if text else "none"
        if any(w in text for w in self.wrong_for):
            intent = "question" if intent != "question" else "start"
        return SituationReport(summary=f"s {intent}", user_intent=intent, user_utterance=text or None,
                               camera_sees="c", change_since_last="c", suspected_fault="f", confidence=0.9)


def slots(n=60, seed=1):
    return md.make_slots(n, seed, "bicycle rim brake", "brake pad rubbing the rim")


# --- slots ------------------------------------------------------------------

def test_slots_are_deterministic_balanced_and_unique():
    a, b = slots(), slots()
    assert [s.id for s in a] == [s.id for s in b]
    assert len({s.id for s in a}) == len(a)
    counts = Counter(s.intent for s in a)
    assert set(counts) == set(md.INTENTS) and max(counts.values()) - min(counts.values()) == 0
    demo = sum(s.demo for s in slots(600))
    assert 0.3 < demo / 600 < 0.5, "about 40% should be the demo object"
    assert all(s.fault in md.OBJECTS[s.object] for s in a if not s.demo)
    assert all(s.object == "bicycle rim brake" and s.fault == "brake pad rubbing the rim" for s in a if s.demo)
    assert slots(60, seed=2)[0].id != a[0].id


def test_slot_phase_rules():
    for s in slots(300):
        if s.intent == "start":
            assert s.phase in ("identifying", "diagnosing")
        if s.intent == "step_done":
            assert s.phase in ("guiding", "verifying")
        assert s.needs_step == (s.phase in ("guiding", "verifying"))
        assert (s.gold_fault is None) == (not s.fault_visible)
        assert s.garbled is False or s.intent != "none"
        assert 1 <= s.n_obs <= 3


def test_changing_a_slot_changes_its_id_so_the_cache_cannot_go_stale():
    a = md.make_slots(30, 1, "bicycle rim brake", "brake pad rubbing the rim")
    b = md.make_slots(30, 1, "bicycle rim brake", "brake pad worn down")
    demo_a = {s.id for s in a if s.demo}
    assert demo_a and not demo_a & {s.id for s in b}


# --- scenario validation ----------------------------------------------------

def one_slot(intent="question", phase="guiding", n_obs=2):
    return md.Slot(id="s0001-abc", object="bicycle rim brake", fault="x", fault_visible=True, intent=intent,
                   phase=phase, n_obs=n_obs, garbled=False, confidence="mid", demo=True)


def scenario(text="which key", step=True, n=3, slot_id="s0001-abc"):
    return md.ScenarioOut(slot_id=slot_id, observations=[obs(1 + i % 3) for i in range(n)], transcript_text=text,
                          current_step=StepBrief(index=1, instruction="i", expected_visual_state="e") if step else None,
                          previous_summary=None)


def test_build_input_trims_observations_and_stamps_frame_ids():
    inp = md.build_input(one_slot(n_obs=2), scenario(n=3))
    assert len(inp.observations) == 2 and [o.frame_id for o in inp.observations] == ["s0001-abc-f1", "s0001-abc-f2"]
    assert inp.transcript.text == "which key" and inp.transcript.duration_s >= 1.0
    assert inp.current_step is not None and inp.phase == "guiding"


def test_build_input_rejects_scenarios_that_dont_fit_the_slot():
    assert md.build_input(one_slot("question"), scenario(text=None)) is None, "speech intent needs a transcript"
    assert md.build_input(one_slot("question"), scenario(text="   ")) is None
    assert md.build_input(one_slot(phase="guiding"), scenario(step=False)) is None, "guiding needs a step"
    assert md.build_input(one_slot(), md.ScenarioOut(slot_id="s", observations=[])) is None


def test_build_input_for_none_intent_drops_any_speech_and_for_early_phases_any_step():
    inp = md.build_input(one_slot("none", "identifying"), scenario(text="should be ignored", step=True))
    assert inp.transcript is None and inp.current_step is None


# --- examples, cache, teacher check -----------------------------------------

def build(stubs, slot_list, tmp_path):
    return run(md.build_examples(slot_list, stubs.scenario_fn, stubs.teacher_fn, tmp_path / "cache"))


def test_build_examples_labels_every_slot(tmp_path):
    st = Stubs()
    sl = slots(24)
    examples, stats = build(st, sl, tmp_path)
    assert len(examples) == 24 and stats["teacher_calls"] == 24
    assert all(e.report.user_intent == e.gold_intent for e in examples)
    assert {e.source for e in examples} == {"synthetic"}


def test_second_run_uses_the_cache_and_makes_no_calls(tmp_path):
    sl = slots(24)
    build(Stubs(), sl, tmp_path)
    st2 = Stubs()
    examples, stats = build(st2, sl, tmp_path)
    assert len(examples) == 24
    assert st2.scenario_calls == 0 and st2.teacher_calls == 0
    assert stats["scenarios_cached"] == 24 and stats["teacher_cached"] == 24


def test_teacher_intent_disagreement_is_dropped(tmp_path):
    sl = [s for s in slots(60) if s.intent == "step_done"][:5]
    st = Stubs(wrong_for=[sl[0].id, sl[1].id])
    examples, stats = build(st, sl, tmp_path)
    assert len(examples) == 3 and stats["dropped_intent_mismatch"] == 2


def test_teacher_errors_and_missing_scenarios_are_dropped_not_fatal(tmp_path):
    sl = [s for s in slots(60) if s.intent == "question"][:5]
    st = Stubs(fail_for=[sl[0].id], drop_scenarios=[sl[1].id])
    examples, stats = build(st, sl, tmp_path)
    assert len(examples) == 3
    assert stats["dropped_teacher_error"] == 1 and stats["dropped_no_scenario"] == 1


def test_input_key_ignores_nothing_that_the_model_sees():
    a = Pass1Input(phase="guiding", observations=[obs(1)])
    b = Pass1Input(phase="guiding", observations=[obs(1)])
    c = Pass1Input(phase="diagnosing", observations=[obs(1)])
    assert md.input_key(a) == md.input_key(b) != md.input_key(c)


# --- real calls -------------------------------------------------------------

def real_row(backend="claude", valid=True, text="hi", intent="question"):
    inp = Pass1Input(phase="guiding", observations=[obs(1)],
                     transcript={"text": text, "language": "en", "duration_s": 1.0})
    rep = SituationReport(summary="s", user_intent=intent, camera_sees="c", change_since_last="c", confidence=0.8)
    return {"backend": backend, "valid_json": valid, "input_json": inp.model_dump_json(), "output_json": rep.model_dump_json()}


def test_load_real_keeps_only_valid_claude_calls_and_dedupes(tmp_path):
    raw = tmp_path / "raw.jsonl"
    rows = [real_row(), real_row(), real_row("adaption"), real_row("heuristic", valid=False),
            real_row(valid=False), real_row(text="other")]
    raw.write_text("\n".join(json.dumps(r) for r in rows) + '\n{"backend": "claude", "valid_js\n')   # last line half-written
    got = md.load_real(raw, store_rows=[real_row(text="from store"), {"backend": "claude", "valid_json": True,
                                                                        "input_json": "garbage", "output_json": "x"}])
    assert sorted(e.inp.transcript.text for e in got) == ["from store", "hi", "other"]
    assert {e.source for e in got} == {"real"}
    assert md.load_real(tmp_path / "missing.jsonl") == []


# --- split ------------------------------------------------------------------

def test_split_is_stratified_disjoint_and_adds_real_to_train_only(tmp_path):
    examples, _ = build(Stubs(), slots(240), tmp_path)
    real = md.load_real(tmp_path / "none.jsonl", store_rows=[real_row(text="real one"), real_row(text="real two")])
    train, ev_rows = md.split(examples, real, eval_size=60, seed=1)
    assert len(ev_rows) == 60
    assert Counter(e.gold_intent for e in ev_rows) == {i: 10 for i in md.INTENTS}
    assert {e.id for e in train}.isdisjoint({e.id for e in ev_rows})
    assert len(train) == len(examples) - 60 + 2
    assert all(e.source == "synthetic" for e in ev_rows)
    assert sum(e.source == "real" for e in train) == 2


def test_split_handles_uneven_eval_size_and_is_reproducible(tmp_path):
    examples, _ = build(Stubs(), slots(240), tmp_path)
    _, e1 = md.split(examples, [], 100, seed=7)
    _, e2 = md.split(examples, [], 100, seed=7)
    assert [e.id for e in e1] == [e.id for e in e2] and len(e1) == 100
    counts = sorted(Counter(e.gold_intent for e in e1).values())
    assert counts == [16, 16, 17, 17, 17, 17]


def test_split_warns_when_an_intent_runs_short(tmp_path, capsys):
    examples, _ = build(Stubs(), slots(24), tmp_path)           # 4 per intent
    _, ev_rows = md.split(examples, [], 60, seed=1)
    assert len(ev_rows) == 24 and "warning" in capsys.readouterr().err


def test_real_rows_matching_eval_inputs_never_reach_train(tmp_path):
    examples, _ = build(Stubs(), slots(60), tmp_path)
    _, ev_rows = md.split(examples, [], 12, seed=1)
    held_out = ev_rows[0]                                       # a real call identical to a held-out input
    leaked = md.Example(id="leak", source="real", inp=held_out.inp, report=held_out.report)
    train, ev_again = md.split(examples, [leaked], 12, seed=1)
    assert {e.id for e in ev_again} == {e.id for e in ev_rows}
    assert "leak" not in {e.id for e in train}


# --- outputs ----------------------------------------------------------------

def args_for(tmp_path, *extra):
    return md.parse_args(["--out-dir", str(tmp_path / "data"), "--n", "120", "--eval-size", "30", "--no-real", *extra])


def test_run_writes_the_three_files_with_serving_identical_prompts(tmp_path):
    st = Stubs()
    assert run(md.run(args_for(tmp_path), st.scenario_fn, st.teacher_fn, store_rows=[])) == 0
    out = tmp_path / "data"
    train = [json.loads(l) for l in (out / "train.jsonl").read_text().splitlines()]
    evals = [json.loads(l) for l in (out / "eval.jsonl").read_text().splitlines()]
    gold = [json.loads(l) for l in (out / "eval_gold.jsonl").read_text().splitlines()]
    assert len(evals) == len(gold) == 30 and len(train) == 90
    for row, g in zip(evals, gold):
        sys_m, user_m, asst_m = row["messages"]
        assert (sys_m["role"], user_m["role"], asst_m["role"]) == ("system", "user", "assistant")
        inp = Pass1Input.model_validate(g["input"])
        assert sys_m["content"] == pass1.load_system_prompt()
        assert user_m["content"] == pass1.render_user(inp), "training text must equal serving text byte for byte"
        label = SituationReport.model_validate_json(asst_m["content"])
        assert label.user_intent == g["intent"]
        assert "\n" not in asst_m["content"]
    for row in train[:5]:
        SituationReport.model_validate_json(row["messages"][2]["content"])


@pytest.mark.parametrize("fmt,keys", [("prompt_completion", {"prompt", "completion"}),
                                      ("alpaca", {"instruction", "input", "output"})])
def test_alternate_formats(tmp_path, fmt, keys):
    st = Stubs()
    run(md.run(args_for(tmp_path, "--format", fmt), st.scenario_fn, st.teacher_fn, store_rows=[]))
    row = json.loads((tmp_path / "data" / "train.jsonl").read_text().splitlines()[0])
    assert set(row) == keys


def test_unknown_format_is_an_error():
    with pytest.raises(ValueError):
        md.to_format([{"content": "a"}, {"content": "b"}, {"content": "c"}], "xml")


def test_without_yes_nothing_is_called_or_written(tmp_path, capsys):
    assert run(md.run(args_for(tmp_path))) == 0
    out = capsys.readouterr().out
    assert "Nothing was called" in out and "rough cost" in out
    assert not (tmp_path / "data" / "train.jsonl").exists()


def test_with_yes_but_no_api_key_stops_cleanly(tmp_path, capsys):
    assert run(md.run(args_for(tmp_path, "--yes"))) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "data" / "train.jsonl").exists()


def test_estimate_cost_is_sane_and_cached_work_is_free():
    assert md.estimate_cost(0, 0) == 0
    assert 0.1 < md.estimate_cost(1, 0) < 0.4
    assert md.estimate_cost(45, 900) < 15


# --- eval: metrics ----------------------------------------------------------

@pytest.mark.parametrize("pred,gold,ok", [
    ("brake pad rubbing the rim", "brake pad rubbing the rim", True),
    ("Brake Pad Rubbing", "brake pad rubbing the rim", True),
    ("the brake pad is rubbing the rim badly", "brake pad is rubbing the rim", True),
    ("worn washer", "brake pad rubbing the rim", False),
    (None, "x", False), ("x", None, False), ("", "x", False), ("  ", "  ", False),
])
def test_fault_matches(pred, gold, ok):
    assert ev.fault_matches(pred, gold) is ok


def gold_rows(n_per_intent=3, with_fault=True):
    rows = []
    for intent in md.INTENTS:
        for i in range(n_per_intent):
            inp = Pass1Input(phase="guiding", observations=[obs(1)])
            rows.append(ev.EvalRow(f"{intent}-{i}", intent, "brake pad rubbing the rim" if with_fault else None, inp))
    return rows


def backend_from(table):
    """table: row index -> ('ok', intent, fault) | ('error',); keyed by call order."""
    calls = {"i": -1}

    async def fn(inp):
        calls["i"] += 1
        spec = table(calls["i"])
        if spec[0] == "error":
            raise pass1.Pass1Error("boom")
        return SituationReport(summary="s", user_intent=spec[1], camera_sees="c", change_since_last="c",
                               suspected_fault=spec[2], confidence=0.5)
    return fn


def test_evaluate_counts_valid_intent_fault_and_latency():
    rows = gold_rows(1)                                             # 6 rows
    spec = {0: ("ok", "start", "brake pad rubbing the rim"), 1: ("ok", "stop", None),          # 1 wrong intent
            2: ("error",), 3: ("ok", "need_help", "brake pad"), 4: ("ok", "stop", "other"),
            5: ("ok", "none", None)}
    m = run(ev.evaluate("claude", backend_from(spec.__getitem__), rows))
    assert (m.n, m.valid) == (6, 5)
    assert m.intent_correct == 4                                    # rows 0,3,4,5 right; 1 wrong; 2 invalid
    assert (m.fault_total, m.fault_correct) == (6, 2)               # rows 0 and 3 contain the gold fault
    assert m.valid_rate == pytest.approx(5 / 6) and m.intent_acc == pytest.approx(4 / 6)
    assert len(m.latencies_ms) == 6 and len(m.errors) == 1 and "boom" in m.errors[0]


def test_rows_without_a_gold_fault_are_not_scored_for_fault():
    m = run(ev.evaluate("c", backend_from(lambda i: ("ok", "start", "x")), gold_rows(1, with_fault=False)))
    assert m.fault_total == 0 and m.fault_rate is None


def test_percentiles():
    m = ev.Metrics("x", latencies_ms=[100, 200, 300, 400, 1000])
    assert m.p50 == 300 and m.p95 == pytest.approx(880)
    assert ev.Metrics("x").p50 == 0.0


# --- eval: the gate ---------------------------------------------------------

def metrics(valid, intent, n=100):
    return ev.Metrics("x", n=n, valid=valid, intent_correct=intent)


@pytest.mark.parametrize("a_valid,a_intent,c_intent,expected", [
    (98, 90, 90, "PASS"),          # exactly 98% valid, equal intent
    (97, 90, 90, "FAIL"),          # valid just under the bar
    (100, 87, 90, "PASS"),         # exactly 3 points behind
    (100, 86, 90, "FAIL"),         # 4 points behind
    (100, 95, 90, "PASS"),         # better than Claude
])
def test_gate_boundaries(a_valid, a_intent, c_intent, expected):
    status, text = ev.verdict(metrics(100, c_intent), metrics(a_valid, a_intent))
    assert status == expected
    assert ("PASS1_BACKEND=adaption" in text) == (expected == "PASS"), text
    if expected == "FAIL":
        assert "PASS1_BACKEND=claude" in text


def test_gate_is_na_without_both_backends():
    assert ev.verdict(metrics(100, 90), None)[0] == "N/A"
    assert ev.verdict(None, metrics(100, 90))[0] == "N/A"


# --- eval: outputs ----------------------------------------------------------

def both():
    return {"claude": ev.Metrics("claude", n=100, valid=100, intent_correct=91, fault_total=50, fault_correct=40,
                                 latencies_ms=[900, 1000, 1100]),
            "adaption": ev.Metrics("adaption", n=100, valid=99, intent_correct=90, fault_total=0,
                                   latencies_ms=[250, 300, 350], errors=["row-1: AdaptionError: bad json"])}


def test_svg_is_valid_xml_with_a_bar_per_value_and_na_for_missing():
    svg = ev.render_svg(both())
    root = ET.fromstring(svg)
    rects = [e for e in root.iter() if e.tag.endswith("rect") and e.get("rx") == "3"]
    assert len(rects) == 5                                          # 2+2 bars, adaption fault match is n/a
    assert "n/a" in svg and "91.0%" in svg and "Claude Haiku 5.5" in svg and "p50" in svg


def test_svg_with_a_single_backend():
    ET.fromstring(ev.render_svg({"claude": both()["claude"]}))


def test_markdown_has_table_verdict_and_errors():
    res = both()
    md_text = ev.render_markdown(res, 100, ev.verdict(res["claude"], res["adaption"]), "2026-11-15 09:00")
    assert "| Claude Haiku 5.5 | 100.0% | 91.0% | 80.0% |" in md_text
    assert "| Adaption fine-tune | 99.0% | 90.0% | n/a |" in md_text
    assert "## Verdict: PASS" in md_text and "results.svg" in md_text and "bad json" in md_text


def test_main_writes_results_and_chart(tmp_path, capsys):
    gold = tmp_path / "eval_gold.jsonl"
    rows = gold_rows(2)
    gold.write_text("\n".join(json.dumps({"id": r.id, "intent": r.intent, "fault": r.fault,
                                          "input": r.inp.model_dump()}) for r in rows))
    good = backend_from(lambda i: ("ok", rows[i % len(rows)].intent, "brake pad rubbing the rim"))
    rc = run(ev.main(["--gold", str(gold), "--results", str(tmp_path / "R.md"), "--chart", str(tmp_path / "c.svg")],
                     backends={"claude": good, "adaption": backend_from(lambda i: ("error",))}))
    assert rc == 0
    out = capsys.readouterr().out
    assert "VERDICT: FAIL" in out
    assert "## Verdict: FAIL" in (tmp_path / "R.md").read_text()
    ET.fromstring((tmp_path / "c.svg").read_text())


def test_main_errors_clearly_without_data_or_keys(tmp_path, capsys):
    assert run(ev.main(["--gold", str(tmp_path / "nope.jsonl")])) == 2
    assert "make_dataset" in capsys.readouterr().err
    gold = tmp_path / "g.jsonl"
    gold.write_text(json.dumps({"id": "a", "intent": "start", "fault": None,
                                "input": Pass1Input(phase="guiding").model_dump()}))
    assert run(ev.main(["--gold", str(gold), "--backends", "claude"])) == 2          # no ANTHROPIC_API_KEY
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err
