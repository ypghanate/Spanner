"""Offline tests for the training / knowledge / sponsor features and their contract adapters.
No API keys or network: everything external is mocked or bypassed."""
from __future__ import annotations

import json

import pytest

from backend import knowledge, parts_agent as pa
from backend.contracts import RepairPlan, RepairStep, SceneObservation
from training import adaption_dataset, feedback, kb, step_tracker as st


# ---------------------------------------------------------------- step tracker (Adaption)
def test_make_input_uses_instruction_and_all_fields():
    text = st.make_input("Clip the cushion on", "cushion flush all round", ["rotated 90 degrees"], "half on", "done")
    assert "STEP: Clip the cushion on" in text and "rotated 90 degrees" in text and "USER SAID: done" in text


def test_parse_output_tolerates_chatter():
    assert st.parse_output('Sure! {"status": "error", "mistake": "upside down"}').status == "error"
    with pytest.raises(ValueError):
        st.parse_output("no json here")


@pytest.mark.parametrize("raw,contract", [("not_started", None), ("in_progress", "in_progress"),
                                          ("done", "done"), ("error", "mistake")])
def test_contract_status_mapping(raw, contract):
    assert st.CONTRACT_STATUS[raw] == contract


def test_step_check_returns_contract_status():
    r = st.step_check("Fit cushion", "cushion flush all round", [], "cushion flush all round", backend="rule")
    assert r["status"] == "done" and r["step_status"] == "done" and r["backend"] == "rule"


@pytest.mark.parametrize("window,step,expected", [((0, 10), (20, 40), "not_started"), ((25, 35), (20, 40), "in_progress"),
                                                  ((45, 55), (20, 40), "done"), ((35, 50), (20, 40), "done")])
def test_dataset_status_rule(window, step, expected):
    assert adaption_dataset._status(*window, *step) == expected


# ---------------------------------------------------------------- Linkup parts agent
def _check(price, verified=True, fits="yes", currency="GBP"):
    offer = pa.Offer(part_name="pads", part_number="X1", retailer="r", price=price, currency=currency,
                     in_stock="yes", url=f"https://shop.example/{price}", compatible_models_listed=[])
    return pa.OfferCheck(offer=offer, verdict=pa.CompatibilityVerdict(fits=fits, reason="", supporting_urls=[]),
                         verified=verified, independent_sources=2 if verified else 0)


def _new(price, currency="GBP"):
    return pa.NewProduct(product_name="H", price=price, currency=currency, retailer="n", url="https://n.example",
                         weight_kg=0.3)


def test_decide_repair_replace_unclear():
    assert pa.decide([_check(18)], _new(279)).verdict == "repair"
    assert pa.decide([_check(250)], _new(279)).verdict == "replace"
    assert pa.decide([_check(18, currency="EUR")], _new(279)).verdict == "unclear"
    assert pa.decide([], _new(279)) is None


def test_decide_prefers_verified_part():
    d = pa.decide([_check(5, verified=False), _check(18)], _new(279))
    assert d.repair_cost == 18 and d.savings == 261


def test_best_chapter_jumps_to_matching_section():
    desc = "0:00 Intro\n1:35 Removing the old ear cushions\n4:10 Cleaning"
    assert pa.best_chapter(desc, "ear cushions", "worn") == (95, "Removing the old ear cushions")
    assert pa.best_chapter("no chapters", "ear cushions", "") is None


def test_mock_card_view_has_badges_decision_and_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(pa, "PARTS_DIR", tmp_path / "parts")
    monkeypatch.setattr(pa, "MANUAL_PROCEDURES_DIR", tmp_path / "manual_procs")
    v = pa.card_view(pa.research("ear cushions", "flaking", model_text="Sony WH-1000XM4", mock=True))
    assert v["options"][0]["badge"].startswith("✓ Fits your Sony WH-1000XM4")
    assert v["decision"]["verdict"] == "repair" and "Saves" in v["decision"]["headline"]
    assert v["tutorial"]["url"].endswith("t=95") and v["as_of"].startswith("Prices as of")


def test_parts_tool_schema_is_valid_for_claude():
    assert pa.TOOL["name"] == "find_replacement_part"
    assert set(pa.TOOL["input_schema"]["required"]) == {"part", "fault", "model"}


# ---------------------------------------------------------------- knowledge -> contracts
@pytest.fixture
def canonical(tmp_path, monkeypatch):
    rec = {"procedure_id": "r1", "repair_id": "r1", "source_kind": "canonical", "source_videos": ["v1"],
           "sources": [{"url": "https://www.youtube.com/watch?v=a", "title": "vid"},
                       {"url": "https://www.ifixit.com/Guide/x", "title": "guide"}],
           "keyframes": {}, "procedure": {
               "object": "headphones", "fault": "worn cushions", "tools_needed": [], "parts": ["cushion"], "notes": "",
               "steps": [{"index": 1, "title": "Remove", "instruction": "Twist the old cushion off.", "parts": ["cushion"],
                          "tools": [], "expected_visual_state": "Bare speaker grille visible on the left cup.",
                          "common_mistakes": ["pulling the cable"], "safety_notes": ["unplug first"],
                          "start_s": 0, "end_s": 90}]}}
    (tmp_path / "data" / "procedures").mkdir(parents=True)
    (tmp_path / "data" / "procedures" / "r1.json").write_text(json.dumps(rec), encoding="utf-8")
    monkeypatch.setattr(knowledge, "ROOT", tmp_path)
    monkeypatch.setattr(kb, "ROOT", tmp_path)
    return rec


def test_plan_from_knowledge_is_a_valid_contract_plan(canonical):
    plan = knowledge.plan_from_knowledge("r1")
    assert isinstance(plan, RepairPlan) and plan.steps[0].instruction == "Twist the old cushion off."
    assert {s.kind for s in plan.sources} == {"youtube", "ifixit"} and plan.safety_notes == ["unplug first"]
    RepairPlan.model_validate(plan.model_dump())  # round-trips through the contract


def test_local_kb_search_finds_step(canonical, monkeypatch):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    hits = kb.search_steps("bare speaker grille cushion", k=1)
    assert hits and hits[0]["step_id"] == "r1#1"


def test_track_step_logs_mistakes_to_learning_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback, "LIVE", tmp_path / "mistakes.jsonl")
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.setattr(knowledge, "step_check", lambda *a, **k: {"status": "error", "mistake": "cable pulled",
                                                                 "step_status": "mistake", "backend": "x", "latency_ms": 1})
    step = RepairStep(index=1, instruction="Twist off", expected_visual_state="grille visible", common_mistakes=[])
    obs = SceneObservation(frame_id="f", object="headphones", state="cable stretched", hands_visible=True, confidence=0.9)
    r = knowledge.track_step(step, obs, session_id="s1", repair_id="r1")
    assert r["step_status"] == "mistake"
    row = json.loads((tmp_path / "mistakes.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert row["session_id"] == "s1" and row["mistake"] == "cable pulled" and row["step_index"] == 1


def test_observation_text_matches_training_wording():
    obs = SceneObservation(frame_id="f", object="o", state="cushion half on", visible_parts=["cushion"],
                           anomalies=[], hands_visible=True, confidence=1)
    assert knowledge.observation_text(obs) == st.describe_observation("cushion half on", "hands working on it", ["cushion"])
